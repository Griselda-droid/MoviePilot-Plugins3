# -*- coding: utf-8 -*-

"""
*************************************************
*** 获取所有已完成的订阅，并显示订阅用户 ***
*************************************************
"""

# 插件加载器会处理路径问题，我们直接从 moviepilot 导入
from moviepilot.plugin import _PluginBase
from moviepilot.utils.subscribe import SubscribeUtils

# 尝试导入用户管理模块，如果失败则优雅降级
try:
    from moviepilot.core.users import Users
    USER_API_AVAILABLE = True
except ImportError:
    USER_API_AVAILABLE = False

# 插件主类，类名 MoviePilot 会自动识别
class Plugin(_PluginBase):
    """
    获取所有已完成的订阅，并显示订阅用户。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._subscribe_utils = SubscribeUtils()
        if USER_API_AVAILABLE:
            self._user_utils = Users()
        else:
            self._user_utils = None
            self.log.warn("无法导入用户管理模块，用户名可能无法正确显示。")

    def _get_username(self, user_id):
        """
        根据用户ID获取用户名。
        """
        if not self._user_utils or not user_id:
            return "未知用户"
        try:
            user = self._user_utils.get(user_id)
            if user and hasattr(user, 'name') and user.name:
                return user.name
            else:
                return f"用户ID:{user_id}"
        except Exception as e:
            self.log.error(f"无法获取 UserID {user_id} 的信息: {e}")
            return f"用户ID:{user_id} (查询出错)"

    def run(self, *args, **kwargs):
        """
        插件的主执行函数。
        """
        self.log.info("开始查询所有已完成的订阅...")

        try:
            all_subscriptions = self._subscribe_utils.get_all()

            if not all_subscriptions:
                self.log.info("数据库中没有任何订阅记录。")
                return

            completed_subs = [
                sub for sub in all_subscriptions if sub.status == 'Downloaded'
            ]

            if not completed_subs:
                self.log.info("未找到已完成的订阅。")
                return

            self.log.info(f"成功找到 {len(completed_subs)} 个已完成的订阅。正在整理输出...")

            output_lines = ["", "--- [ 已完成的订阅列表 ] ---"]
            for sub in completed_subs:
                title = sub.get_title()
                user_name = self._get_username(sub.user_id)
                output_lines.append(f"  - 媒体标题: {title}")
                output_lines.append(f"  - 订阅用户: {user_name}")
                output_lines.append("  ------------------------")
            
            # 使用多行输出来避免日志过长被截断
            self.log.info("\n".join(output_lines))
            self.log.info("所有已完成订阅查询结束。")

        except Exception as e:
            self.log.error(f"执行插件时发生未知错误: {e}", exc_info=True)
