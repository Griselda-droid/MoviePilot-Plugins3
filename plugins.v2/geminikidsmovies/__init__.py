# -*- coding: utf-8 -*-

"""
*************************************************
***      Gemini 儿童电影推荐 (GeminiKidsMovies)     ***
*************************************************
- 功能：通过 AI (Gemini) 获取近期适合儿童的电影，并自动添加订阅。
- 作者：Gemini & 用户
- 规范：严格参照系统数据模型和范例插件结构编写。
"""

# 基础库导入
import re
import json
from typing import Any, Dict, List, Tuple
from datetime import datetime
import requests

# 第三方库导入
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

# MoviePilot 核心模块导入
from app.log import logger
from app.plugins import _PluginBase
from app.db.models.subscribehistory import SubscribeHistory
from app.db import db_query
from app.db.subscribe_oper import SubscribeOper
from app.chain.subscribe import SubscribeChain
from app.core.metainfo import MetaInfo
from app.schemas import NotificationType
from app.schemas.types import MediaType


class GeminiKidsMovies(_PluginBase):
    """
    插件的主类，继承自 _PluginBase。
    """
    # 插件元信息
    plugin_name = "Gemini儿童电影推荐"
    plugin_desc = "通过AI（如Gemini）获取近期适合儿童的电影，并自动添加订阅。"
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/gemini.png"
    plugin_version = "1.5.1" # 修正正则表达式以兼容多种AI输出格式
    plugin_author = "Gemini & 用户"
    author_url = "https://github.com/InfinityPacer/MoviePilot-Plugins"
    plugin_config_prefix = "gemini_kids_"
    auth_level = 1
    
    # 私有属性
    _enabled: bool = False
    _notify: bool = False
    _cron: str = None
    _onlyonce: bool = False
    _api_key: str = ""
    _model_name: str = ""
    _user_prompt: str = ""
    _final_prompt: str = ""
    _save_path: str = ""
    _sites: List[int] = []
    _initialized: bool = False

    # 操作类实例
    subscribe_oper: SubscribeOper = None
    subscribe_chain: SubscribeChain = None

    def init_plugin(self, config: dict = None):
        """
        插件初始化
        """
        self.subscribe_oper = SubscribeOper()
        self.subscribe_chain = SubscribeChain()
        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)
            self._api_key = config.get("api_key", "")
            self._model_name = config.get("model_name", "gemini-1.5-flash")
            
            self._user_prompt = config.get("prompt", "")
            
            default_prompt = self._get_default_prompt()
            if self._user_prompt and self._user_prompt.strip():
                self._final_prompt = f"{default_prompt}\n\n用户的额外要求：\n{self._user_prompt}"
            else:
                self._final_prompt = default_prompt

            self._save_path = config.get("save_path", "")
            self._sites = config.get("sites", [])
        
        if not self._initialized:
            logger.info(f"【{self.plugin_name}】插件配置加载完成。")
            logger.debug(f" - 启用状态: {self._enabled}")
            logger.debug(f" - API 密钥: {'已设置' if self._api_key else '未设置'}")
            logger.debug(f" - 模型名称: {self._model_name}")
            logger.debug(f" - 用户输入Prompt: {self._user_prompt}")
            self._initialized = True
        
        self.__update_config()

        if self._onlyonce:
            logger.info(f"【{self.plugin_name}】：配置了“立即运行一次”，任务即将开始...")
            self.run_check()
            self._onlyonce = False
            self.__update_config()

    # ... (get_state, get_service, get_command, get_api, get_page, get_form, stop_service, _is_in_history, _call_gemini_api 等方法保持不变)
    def get_state(self) -> bool:
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        if not self.get_state(): return []
        if self._cron:
            return [{"id": f"{self.__class__.__name__}_check", "name": "Gemini电影推荐", "trigger": CronTrigger.from_crontab(self._cron), "func": self.run_check, "kwargs": {}}]
        else:
            return [{"id": f"{self.__class__.__name__}_check_default", "name": "Gemini电影推荐 (默认)", "trigger": "cron", "func": self.run_check, "kwargs": {"day_of_week": "fri", "hour": 20, "minute": 0}}]

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []
        
    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_page(self) -> List[dict]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        from app.db.site_oper import SiteOper
        sites_options = [{"title": site.name, "value": site.id} for site in SiteOper().list_order_by_pri()]
        return [
            {'component': 'VForm', 'content': [
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'api_key', 'label': 'Gemini API 密钥', 'type': 'password', 'hint': '请输入您的Google AI Studio API密钥', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'model_name', 'label': '模型名称', 'hint': '默认为 gemini-1.5-flash', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextarea', 'props': {'model': 'prompt', 'label': 'AI Prompt (额外要求)', 'rows': 5, 'hint': '留空则使用内置的默认提问。如果填写，您的内容将作为额外要求附加到默认提问之后。', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'save_path', 'label': '保存路径', 'hint': '新增订阅使用的保存路径，留空则使用默认', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSelect', 'props': {'model': 'sites', 'label': '订阅站点', 'chips': True, 'multiple': True, 'clearable': True, 'items': sites_options, 'hint': '新增订阅时要搜索的站点', 'persistent-hint': True}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VCronField', 'props': {'model': 'cron', 'label': '执行周期 (CRON)', 'hint': '留空则每周五晚8点执行', 'persistent-hint': True}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 2}, 'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 2}, 'content': [{'component': 'VSwitch', 'props': {'model': 'notify', 'label': '通知'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 2}, 'content': [{'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '运行一次'}}]}
                ]},
            ]}
        ], self.get_config_dict()

    def stop_service(self):
        pass
        
    @db_query
    def _is_in_history(self, db: Session, tmdbid: int) -> bool:
        return db.query(SubscribeHistory).filter(SubscribeHistory.tmdbid == tmdbid).first() is not None

    def _call_gemini_api(self) -> str:
        logger.info(f"正在通过 HTTP 请求调用 Gemini API，使用模型: {self._model_name}...")
        logger.info(f"发送给 API 的完整 Prompt 内容: \n---PROMPT START---\n{self._final_prompt}\n---PROMPT END---")
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model_name}:generateContent?key={self._api_key}"
        headers = {'Content-Type': 'application/json'}
        payload = {"contents": [{"parts": [{"text": self._final_prompt}]}]}
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            logger.debug(f"收到 Gemini API 的原始响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
            text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            if not text:
                logger.warning("Gemini API 返回了成功状态，但未能提取到文本内容。")
                return ""
            logger.info("成功获取并解析 Gemini API 的响应。")
            return text
        except requests.exceptions.RequestException as e:
            if e.response is not None and e.response.status_code == 404:
                logger.error(f"调用 Gemini API 时发生 404 Not Found 错误，请检查您的“模型名称”({self._model_name})是否正确且可用。", exc_info=True)
            else:
                logger.error(f"调用 Gemini API 时发生网络错误: {e}", exc_info=True)
            return ""
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.error(f"解析 Gemini API 响应时发生错误: {e}", exc_info=True)
            return ""

    def _parse_movie_list(self, text: str) -> List[Tuple[str, str]]:
        """
        使用正则表达式解析AI返回的文本，提取电影名和年份。
        """
        logger.info(f"开始解析 AI 响应文本...")
        # 致命修正：使用一个更宽容的正则表达式，它不再强制要求行首必须有 '*'。
        # 它会匹配可选的行首空白、可选的'*'号、可选的书名号，然后捕获电影名和年份。
        pattern = re.compile(r"^\s*(?:\*\s*)?《?(.+?)》?\s*\((\d{4})\)", re.MULTILINE)
        matches = pattern.findall(text)
        if not matches:
            logger.warning("从AI的响应中未能解析出任何 '电影 (年份)' 格式的条目。")
        return matches

    def run_check(self):
        logger.info(f"开始执行【{self.plugin_name}】任务...")
        if not self._api_key or not self._final_prompt:
            logger.error(f"【{self.plugin_name}】：API密钥或Prompt为空，任务中止。")
            return
        ai_response_text = self._call_gemini_api()
        if not ai_response_text: return
        movies_to_subscribe = self._parse_movie_list(ai_response_text)
        if not movies_to_subscribe: return
        logger.info(f"从AI响应中成功解析出 {len(movies_to_subscribe)} 部电影，开始处理...")
        added_count = 0
        skipped_count = 0
        for title, year in movies_to_subscribe:
            title = title.strip()
            logger.info(f"--- 正在处理: {title} ({year}) ---")
            try:
                meta = MetaInfo(title)
                meta.year = year
                mediainfo = self.chain.recognize_media(meta=meta, mtype=MediaType.MOVIE)
                if not mediainfo or not mediainfo.tmdb_id:
                    logger.warning(f"无法识别媒体信息: {title} ({year})")
                    skipped_count += 1
                    continue
                if self.subscribe_oper.list_by_tmdbid(tmdbid=mediainfo.tmdb_id):
                    logger.info(f"'{title}' 已经存在于活跃订阅中，跳过。")
                    skipped_count += 1
                    continue
                if self._is_in_history(tmdbid=mediainfo.tmdb_id):
                    logger.info(f"'{title}' 已经存在于订阅历史中，跳过。")
                    skipped_count += 1
                    continue
                logger.info(f"'{title}' 是新电影，准备添加订阅...")
                sid, msg = self.subscribe_chain.add(
                    title=mediainfo.title,
                    year=mediainfo.year,
                    mtype=MediaType.MOVIE,
                    tmdbid=mediainfo.tmdb_id,
                    username=self.plugin_name,
                    save_path=self._save_path,
                    sites=self._sites,
                    exist_ok=True
                )
                if sid:
                    logger.info(f"成功添加订阅: '{title}' (ID: {sid})")
                    added_count += 1
                else:
                    logger.error(f"添加订阅 '{title}' 失败: {msg}")
                    skipped_count += 1
            except Exception as e:
                logger.error(f"处理 '{title}' ({year}) 时发生未知错误: {e}", exc_info=True)
                skipped_count += 1
        summary_text = f"任务完成，共成功添加 {added_count} 部电影订阅，跳过 {skipped_count} 部（已存在或识别失败）。"
        logger.info(f"【{self.plugin_name}】{summary_text}")
        if self._notify and added_count > 0:
            self.post_message(
                mtype=NotificationType.Plugin,
                title=f"【{self.plugin_name}】执行完成",
                text=summary_text
            )

    def get_config_dict(self):
        return { 
            "enabled": self._enabled, 
            "notify": self._notify, 
            "cron": self._cron, 
            "onlyonce": self._onlyonce, 
            "api_key": self._api_key,
            "model_name": self._model_name,
            "prompt": self._user_prompt,
            "save_path": self._save_path,
            "sites": self._sites
        }
    
    def __update_config(self):
        self.update_config(self.get_config_dict())
        
    def _get_default_prompt(self):
        """
        致命修正：返回一个经过优化的、要求使用TMDB或豆瓣官方译名的高质量默认Prompt。
        """
        today_str = datetime.now().strftime('%Y年%m月%d日')
        return (f"今天是 {today_str}。\n"
                "请你扮演一位专业的影视推荐专家。\n"
                "请推荐5部 **已经发行的，或者即将在未来3个月内上映的**、适合全家观看的儿童动画电影。\n"
                "要求：\n"
                "1. 电影名称必须是它在 TheMovieDB (TMDB) 或豆瓣电影上的**官方简体中文译名**。\n"
                "2. 严格按照'《电影名》(年份)'的格式返回，每部电影占一行，不要有任何多余的文字或列表符号。")
