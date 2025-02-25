import requests
import json
import configparser
import os
import traceback
import logging
from typing import List, Dict, Any

# 配置日志格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

CONFIG_PATH = "./c.ini"
MAX_RETRIES = 3
REQUEST_TIMEOUT = 10

class ConfigManager:
    """配置管理器"""
    def __init__(self):
        self.config = configparser.ConfigParser()
        if not os.path.exists(CONFIG_PATH):
            raise FileNotFoundError(f"配置文件 {CONFIG_PATH} 不存在")
            
        self.config.read(CONFIG_PATH, encoding="utf-8")
        
    def get_credentials(self) -> Dict[str, str]:
        """获取认证凭证"""
        return {
            'secret': self.get_with_check('main', 'secret'),
            'key_session': self.get_with_check('main', 'key_session')
        }
        
    def get_with_check(self, section: str, key: str) -> str:
        """安全获取配置项"""
        if not self.config.has_section(section):
            raise ValueError(f"配置文件中缺少 [{section}] 节")
            
        if not self.config.has_option(section, key):
            raise ValueError(f"配置文件中缺少 {key} 选项")
            
        return self.config.get(section, key)

class LearningClient:
    """学习平台客户端"""
    def __init__(self, credentials: Dict[str, str]):
        self.base_url = "https://dekt.hfut.edu.cn/scReports/api/wx/netlearning"
        self.session = requests.Session()
        self._init_headers(credentials)
        
    def _init_headers(self, credentials: Dict[str, str]):
        """初始化请求头"""
        self.session.headers = {
            'Host': 'dekt.hfut.edu.cn',
            'Connection': 'keep-alive',
            'secret': credentials['secret'],
            'key_session': credentials['key_session'],
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF XWEB/8431',
            'Content-Type': 'application/json',
            'Accept': '*/*',
        }
        
    def safe_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """带重试机制的请求方法"""
        for _ in range(MAX_RETRIES):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    timeout=REQUEST_TIMEOUT,
                    **kwargs
                )
                response.raise_for_status()
                return response
            except (requests.RequestException, ConnectionError) as e:
                logging.warning(f"请求失败: {str(e)}, 剩余重试次数: {MAX_RETRIES - _ - 1}")
        raise ConnectionError(f"请求失败，已达最大重试次数: {url}")

class AnswerSystem:
    """自动化答题系统"""
    def __init__(self, client: LearningClient):
        self.client = client
        self.data = {"category": "", "columnType": "0"}
        self.should_stop = False
        
    @staticmethod
    def generate_combinations(n: int) -> List[List[int]]:
        """生成所有可能的答案组合"""
        def backtrack(start: int, path: List[int]):
            if path:
                result.append(path.copy())
            for i in range(start, n):
                path.append(i)
                backtrack(i + 1, path)
                path.pop()
                
        result = []
        backtrack(0, [])
        return result
        
    def process_article(self, article_id: str, ignore_status: bool = False) -> bool:
        """处理单个文章"""
        logging.info(f"处理文章: {article_id}")
        
        # 获取题目数据
        try:
            questions_url = f"{self.client.base_url}/questions/{article_id}"
            response = self.client.safe_request("GET", questions_url)
            questions_data = response.json()["data"]
        except Exception as e:
            logging.error(f"获取题目失败: {str(e)}")
            return -1

        # 检查答题状态
        if not ignore_status:
            if questions_data.get("accquieCredit", False):
                logging.info("已获得学分，跳过")
                return -1
            if questions_data.get("todayReach", False):
                logging.warning("今日已达上限，停止答题")
                self.should_stop = True
                return 0

        # 处理每个题目
        for question in questions_data.get("questions", []):
            if not self._process_question(question):
                return 1
        return True
        
    def _process_question(self, question: Dict[str, Any]) -> bool:
        """处理单个题目"""
        logging.info(f"处理题目: {question['id']} ({self._get_question_type(question)})")
        
        options = question["optionList"]
        correct_found = False
        
        # 根据题型生成答案组合
        if question["queType"] == 1:  # 多选题
            combinations = self.generate_combinations(len(options))
        else:  # 单选题
            combinations = [[i] for i in range(len(options))]
            
        for combo in combinations:
            answer_ids = [options[i]["id"] for i in combo]
            answer_desc = "/".join([options[i]["optionContent"] for i in combo])
            
            logging.debug(f"尝试答案组合: {answer_desc}")
            if self._submit_answer(question["id"], answer_ids):
                correct_found = True
                break
                
        if not correct_found:
            logging.error("未找到正确答案")
            return False
        return True
        
    def _submit_answer(self, question_id: str, answer_ids: List[str]) -> bool:
        """提交答案"""
        try:
            url = f"{self.client.base_url}/answer/{question_id}"
            response = self.client.safe_request("POST", url, data=json.dumps(answer_ids))
            result = response.json()["data"]
            logging.info(f"答题结果: {result['desc']}")
            return "正确" in result["desc"] or "恭喜" in result["desc"]
        except Exception as e:
            logging.error(f"提交答案失败: {str(e)}")
            return False
            
    @staticmethod
    def _get_question_type(question: Dict) -> str:
        """获取题目类型描述"""
        types = {0: "单选题", 1: "多选题"}
        return types.get(question["queType"], "未知题型")
        
    def run(self):
        """执行主流程"""
        logging.info("开始处理学习任务")
        page = 1
        while self.should_stop == False:
            try:
                self._process_page(page)
            except Exception as e:
                logging.error(f"处理第 {page} 页时出错: {str(e)}")
                break
            
            page += 1
                
    def _process_page(self, page: int):
        """处理单个页面"""
        url = f"{self.client.base_url}/page/{page}/10"
        response = self.client.safe_request("POST", url, data=json.dumps(self.data))
        articles = response.json()["data"]["list"]
        
        for article in articles:
            if self._should_process(article):
                self.process_article(article["id"])
            if self.should_stop:
                break
                
    @staticmethod
    def _should_process(article: Dict) -> bool:
        """判断是否需要处理该文章"""
        if article.get("videoUrl"):
            logging.info(f"跳过视频文章: {article['id']}")
            return False
        if article.get("correct") == "已完成":
            logging.info(f"文章已完成: {article['id']}")
            return False
        return True

if __name__ == "__main__":
    try:
        # 初始化配置
        config = ConfigManager()
        credentials = config.get_credentials()
        
        # 创建客户端
        client = LearningClient(credentials)
        system = AnswerSystem(client)
        
        # 用户确认
        logging.info("---- 学习自动化脚本启动 ----")
            
        # 执行主流程
        system.run()
        logging.info("任务执行完成")
        
    except Exception as e:
        logging.error(f"程序异常: {str(e)}")
        logging.debug(traceback.format_exc())