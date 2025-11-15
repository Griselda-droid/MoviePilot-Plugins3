# -*- coding: utf-8 -*-
"""
*************************************************
***      Gemini 儿童电影推荐 (GeminiKidsMovies)     ***
*************************************************
- 功能：通过 AI (Gemini) 获取近期适合儿童的电影，并自动添加订阅。
- 作者：Gemini & 用户（修正版）
- 说明：修复了导致详情页面空白和运行异常的若干问题，增强了容错性。
"""

# 基础库导入
import re
import json
import time
from typing import Any, Dict, List, Tuple, Optional
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
from app.chain.download import DownloadChain
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
    plugin_version = "1.7.6"
    plugin_author = "Gemini & 用户"
    author_url = "https://github.com/InfinityPacer/MoviePilot-Plugins"
    plugin_config_prefix = "gemini_kids_"
    auth_level = 1

    # 私有属性
    _enabled: bool = False
    _notify: bool = False
    _cron: Optional[str] = None
    _onlyonce: bool = False
    _api_key: str = ""
    _user_prompt: str = ""
    _final_prompt: str = ""
    _save_path: str = ""
    _sites: List[int] = []
    _initialized: bool = False

    # 操作类实例
    subscribe_oper: SubscribeOper = None
    subscribe_chain: SubscribeChain = None
    chain = None  # 识别媒体的链（可能在运行时由外部框架注入）

    def init_plugin(self, config: dict = None):
        """
        插件初始化
        """
        self.subscribe_oper = SubscribeOper()
        self.subscribe_chain = SubscribeChain()
        # DownloadChain 用于检查媒体库中是否已存在
        self._download_chain = DownloadChain()

        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", False)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)
            self._api_key = config.get("api_key", "")

            self._user_prompt = config.get("prompt", "")

            default_prompt = self._get_default_prompt()
            if self._user_prompt and self._user_prompt.strip():
                self._final_prompt = f"{default_prompt}\n\n用户的额外要求：\n{self._user_prompt}"
            else:
                self._final_prompt = default_prompt

            self._save_path = config.get("save_path", "")
            self._sites = config.get("sites", [])

        # 尝试从宿主框架获取媒体识别链（如果框架在外部注入）
        try:
            if hasattr(self, 'chain') and self.chain:
                pass
        except Exception:
            self.chain = None

        if not self._initialized:
            logger.info(f"【{self.plugin_name}】插件配置加载完成。")
            self._initialized = True

        self.__update_config()

        if self._onlyonce:
            logger.info(f"【{self.plugin_name}】：配置了“立即运行一次”，任务即将开始...")
            try:
                self.run_check()
            except Exception as e:
                logger.error(f"立即运行一次过程中发生错误: {e}", exc_info=True)
            self._onlyonce = False
            self.__update_config()

    def get_state(self) -> bool:
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        if not self.get_state():
            return []
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
        """
        插件详情页：展示本插件**新增订阅**的历史记录（支持搜索、按年过滤、分页及卡片样式优化）。
        注意：历史记录保存在 key `added_history` 中，每条记录应包含至少：title, year, image, add_time。
        """
        # 读取已添加的订阅历史（由 run_check 保存）
        added_history = self.get_data('added_history') or []

        if not added_history:
            return [
                {'component': 'div', 'text': '暂无添加记录', 'props': {'class': 'text-center text-h6 pa-4'}}
            ]

        # 保证按添加时间倒序显示
        try:
            added_history = sorted(added_history, key=lambda x: x.get('add_time', ''), reverse=True)
        except Exception:
            # 若排序失败则忽略
            pass

        # 前端每页条目数
        items_per_page = 200

        # 构建可选年份列表（用于前端筛选）
        years = []
        for it in added_history:
            y = it.get('year') if isinstance(it, dict) else None
            if y and y not in years:
                years.append(y)
        years = sorted(years, reverse=True)

        # 返回页面结构：包含搜索框、年份下拉、VWindow + VPagination 展示卡片
        return [
            {
                'component': 'div',
                'content': [
                    # 搜索栏与筛选
                    {
                        'component': 'div',
                        'props': {'class': 'd-flex flex-no-wrap pa-4'},
                        'content': [
                            {
                                'component': 'VTextField',
                                'props': {
                                    'model': '_search',
                                    'label': '搜索（按片名）',
                                    'clearable': True,
                                    'class': 'flex-grow-1 mr-4'
                                }
                            },
                            {
                                'component': 'VSelect',
                                'props': {
                                    'model': '_year',
                                    'items': years,
                                    'label': '筛选年份',
                                    'clearable': True,
                                    'class': 'mr-4',
                                    'dense': True
                                }
                            },
                            {
                                'component': 'VBtn',
                                'props': {'text': True, 'icon': True, 'class': 'mx-0'},
                                'content': [
                                    {'component': 'VIcon', 'text': 'mdi-magnify'}
                                ]
                            }
                        ]
                    },

                    # VWindow: 每一页一个 VWindowItem
                    {
                        'component': 'VWindow',
                        'props': {'model': '_page'},
                        'content': self._build_added_history_pages(added_history, items_per_page)
                    },

                    # 分页器
                    {
                        'component': 'div',
                        'props': {'class': 'd-flex justify-center pa-4'},
                        'content': [
                            {
                                'component': 'VPagination',
                                'props': {
                                    'model': '_page',
                                    'length': (len(added_history) - 1) // items_per_page + 1
                                }
                            }
                        ]
                    }
                ]
            }
        ]



    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        from app.db.site_oper import SiteOper
        try:
            sites_options = [{"title": site.name, "value": site.id} for site in SiteOper().list_order_by_pri()]
        except Exception as e:
            logger.error(f"获取站点列表失败: {e}", exc_info=True)
            sites_options = []

        return [
            {'component': 'VForm', 'content': [
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextField', 'props': {'model': 'api_key', 'label': 'Gemini API 密钥', 'type': 'password', 'hint': '请输入您的Google AI Studio API密钥', 'persistent-hint': True}}]},
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

    def _call_gemini_api(self) -> str:
        # 模型名称（固定）
        model_name = "gemini-2.5-flash"
        logger.info(f"正在通过 HTTP 请求调用 Gemini API，使用模型: {model_name}...")
        logger.debug(f"发送给 API 的完整 Prompt 内容: \n---PROMPT START---\n{self._final_prompt}\n---PROMPT END---")

        if not self._api_key:
            logger.error("API Key 为空，无法调用 Gemini API。")
            return ""

        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self._api_key}"
        headers = {'Content-Type': 'application/json'}
        payload = {"contents": [{"parts": [{"text": self._final_prompt}]}]}
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            # 兼容不同结构的返回
            text = ""
            try:
                text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            except Exception:
                # 兼容其他嵌套结构
                for k in ('output', 'content', 'candidates'):
                    if isinstance(result, dict) and k in result:
                        # 尝试简单提取第一个字符串
                        try:
                            text = str(result[k])
                            break
                        except Exception:
                            continue

            if not text:
                logger.warning("Gemini API 返回了成功状态，但未能提取到文本内容。原始响应: %s", json.dumps(result)[:1000])
                return ""

            logger.info("成功获取 Gemini API 的响应文本（已截断）")
            return text
        except requests.exceptions.RequestException as e:
            logger.error(f"调用 Gemini API 时发生网络错误: {e}", exc_info=True)
            return ""
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.error(f"解析 Gemini API 响应时发生错误: {e}", exc_info=True)
            return ""

    def _parse_movie_list(self, text: str) -> List[Tuple[str, str, str]]:
        logger.info("开始解析 AI 响应文本...")
        # 支持多种可能的格式，保证尽量解析到电影条目
        pattern = re.compile(r"^\s*(?:\*\s*)?《?(.+?)》?\s*\((\d{4})\)\s*\(TMDB ID:\s*(\d+)\)", re.MULTILINE)
        matches = pattern.findall(text)
        if not matches:
            logger.warning("从AI的响应中未能解析出任何 '《电影名》(年份) (TMDB ID: xxxxx)' 格式的条目。将尝试更加宽松的解析。")
            # 更宽松的解析：尝试只解析标题和年份
            loose_pattern = re.compile(r"《?(.+?)》?\s*\((\d{4})\)")
            loose = loose_pattern.findall(text)
            results = []
            for title, year in loose:
                # TMDB ID 缺失时用 0 占位
                results.append((title.strip(), year, '0'))
            return results
        return matches

    def _recognize_media(self, meta: MetaInfo, mtype: MediaType):
        """
        统一的媒体识别入口：优先使用外部注入的 self.chain（若存在），否则尝试系统常用的方法。
        返回一个具有 tmdb_id, title, year, get_poster_image 方法的对象，或 None。
        """
        try:
            if hasattr(self, 'chain') and self.chain and hasattr(self.chain, 'recognize_media'):
                return self.chain.recognize_media(meta=meta, mtype=mtype)
        except Exception as e:
            logger.warning(f"通过注入的 chain 识别媒体失败: {e}")

        # 回退：尽量通过 MetaInfo 本身返回一个最小对象
        class _Fallback:
            def __init__(self, metaobj: MetaInfo):
                self.title = getattr(metaobj, 'title', '')
                self.year = getattr(metaobj, 'year', '')
                self.tmdb_id = getattr(metaobj, 'tmdb_id', None) or None
            def get_poster_image(self):
                return ''

        return _Fallback(meta)

    def run_check(self):
        logger.info(f"开始执行【{self.plugin_name}】任务...")
        if not self._api_key or not self._final_prompt:
            logger.error(f"【{self.plugin_name}】：API密钥或Prompt为空，任务中止。")
            return

        ai_response_text = self._call_gemini_api()
        if not ai_response_text:
            logger.info("AI 返回为空，结束本次任务。")
            return

        movies_to_subscribe = self._parse_movie_list(ai_response_text)
        if not movies_to_subscribe:
            logger.info("没有解析到可订阅的电影，结束本次任务。")
            return

        added_count = 0
        skipped_count = 0
        newly_added_items = []

        for title, year, ai_tmdb_id in movies_to_subscribe:
            title = title.strip()
            try:
                ai_tmdb_id = int(ai_tmdb_id)
            except Exception:
                ai_tmdb_id = 0

            logger.info(f"--- 正在处理: {title} ({year}) [AI提供 TMDB ID: {ai_tmdb_id}] ---")
            try:
                meta = MetaInfo(title)
                meta.year = year

                mediainfo = self._recognize_media(meta=meta, mtype=MediaType.MOVIE)

                if not mediainfo or not getattr(mediainfo, 'tmdb_id', None):
                    logger.warning(f"无法通过标题 '{title} ({year})' 识别媒体信息，跳过。")
                    skipped_count += 1
                    continue

                # 若 AI 提供的 ID 与系统识别不同，记录警告但使用系统识别结果
                if getattr(mediainfo, 'tmdb_id', None) and ai_tmdb_id and mediainfo.tmdb_id != ai_tmdb_id:
                    logger.warning(f"系统识别的 TMDB ID '{mediainfo.tmdb_id}' 与 AI 提供的 '{ai_tmdb_id}' 不匹配，将使用系统识别结果。")

                # 已存在订阅检查
                try:
                    if self.subscribe_oper.list_by_tmdbid(tmdbid=mediainfo.tmdb_id):
                        logger.info(f"'{mediainfo.title}' 已经存在于活跃订阅中，跳过。")
                        skipped_count += 1
                        continue
                except Exception as e:
                    logger.warning(f"查询订阅时发生错误（继续尝试添加）: {e}")

                # 已存在媒体库检查
                try:
                    exist_flag, _ = self._download_chain.get_no_exists_info(meta=meta, mediainfo=mediainfo)
                    if exist_flag:
                        logger.info(f"'{mediainfo.title}' 已经存在于媒体库中，跳过。")
                        skipped_count += 1
                        continue
                except Exception as e:
                    logger.warning(f"检查媒体库存在性时出错（继续尝试添加）: {e}")

                logger.info(f"'{mediainfo.title}' 是新电影，准备添加订阅...")
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
                    logger.info(f"成功添加订阅: '{mediainfo.title}' (ID: {sid})")
                    added_count += 1
                    newly_added_items.append({
                        "title": mediainfo.title,
                        "year": mediainfo.year,
                        "image": getattr(mediainfo, 'get_poster_image', lambda: '')(),
                        "add_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    })
                else:
                    logger.error(f"添加订阅 '{mediainfo.title}' 失败: {msg}")
                    skipped_count += 1
            except Exception as e:
                logger.error(f"处理 '{title}' ({year}) 时发生未知错误: {e}", exc_info=True)
                skipped_count += 1

        if newly_added_items:
            try:
                all_added_history = self.get_data('added_history') or []
                all_added_history.extend(newly_added_items)
                self.save_data('added_history', all_added_history[-1000:])
            except Exception as e:
                logger.error(f"保存已添加历史时出错: {e}", exc_info=True)

        summary_text = f"任务完成，共成功添加 {added_count} 部电影订阅，跳过 {skipped_count} 部。"
        logger.info(f"【{self.plugin_name}】{summary_text}")
        if self._notify and added_count > 0:
            try:
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title=f"【{self.plugin_name}】执行完成",
                    text=summary_text
                )
            except Exception as e:
                logger.warning(f"发送通知失败: {e}")

    def get_config_dict(self):
        return {
            "enabled": self._enabled,
            "notify": self._notify,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "api_key": self._api_key,
            "prompt": self._user_prompt,
            "save_path": self._save_path,
            "sites": self._sites
        }

    def __update_config(self):
        try:
            self.update_config(self.get_config_dict())
        except Exception as e:
            logger.error(f"更新插件配置到宿主时出错: {e}", exc_info=True)

    def _get_default_prompt(self):
        today_str = datetime.now().strftime('%Y年%m月%d日')
        return (f"今天是 {today_str}。\n"
                "请你扮演一位专业的影视推荐专家。\n"
                "请推荐5部 **已经上线发行的高评分，或者即将在未来3个月内上映的**、适合全家观看的儿童动画电影。\n"
                "要求：\n"
                "1. 电影名称必须是它在 TheMovieDB (TMDB) 上的原始标题 (original_title)。\n"
                "2. 严格按照 '《电影名》(年份) (TMDB ID: xxxxx)' 的格式返回，每部电影占一行，不要有任何多余的文字或列表符号。")
