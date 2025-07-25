# -*- coding: utf-8 -*-

from moviepilot.plugin import _PluginBase
from moviepilot.utils.subscribe import SubscribeUtils

# 假设的数据库接口，用于获取用户信息
# 实际的接口可能需要根据 MoviePilot 的具体实现来调整
try:
    from moviepilot.core.users import Users
    USER_API_AVAILABLE = True
except ImportError:
    USER_API_AVAILABLE = False


class CompletedSubscriptions(_PluginBase):
    """
    获取所有已完成的订阅，并显示订阅用户。
    """

    # 插件的基础信息，会显示在 MoviePilot 的插件市场中
    name = "completed_subscriptions_viewer"
    version = "1.0"
    description = "获取并显示所有已完成的订阅及其订阅用户。"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 实例化订阅工具类，用于访问订阅数据
        self._subscribe_utils = SubscribeUtils()
        if USER_API_AVAILABLE:
            self._user_utils = Users()
        else:
            self._user_utils = None

    def _get_username(self, user_id):
        """
        根据用户ID获取用户名。
        这是一个辅助函数，用于解耦用户信息的获取逻辑。
        """
        if not self._user_utils or not user_id:
            return "未知用户"
        try:
            user = self._user_utils.get(user_id)
            if user and user.name:
                return user.name
            else:
                return f"用户ID:{user_id}"
        except Exception as e:
            self.log.error(f"无法获取 UserID {user_id} 的信息: {e}")
            return f"用户ID:{user_id} (查询出错)"

    def run(self, *args, **kwargs):
        """
        插件的主执行函数，当任务被触发时，MoviePilot 会调用此函数。
        """
        self.log.info("开始查询所有已完成的订阅...")

        try:
            # 1. 从订阅数据库中获取所有订阅
            all_subscriptions = self._subscribe_utils.get_all()

            if not all_subscriptions:
                self.log.info("数据库中没有任何订阅记录。")
                return

            # 2. 筛选出状态为 "Downloaded" (已完成) 的订阅
            completed_subs = [
                sub for sub in all_subscriptions if sub.status == 'Downloaded'
            ]

            if not completed_subs:
                self.log.info("未找到已完成的订阅。")
                return

            self.log.info(f"成功找到 {len(completed_subs)} 个已完成的订阅。正在整理输出...")

            # 3. 格式化并输出结果
            output_message = "\n--- [ 已完成的订阅列表 ] ---\n"
            for sub in completed_subs:
                # 获取订阅的标题，优先使用中文标题
                title = sub.get_title()
                # 根据订阅记录中的 user_id 获取用户名
                user_name = self._get_username(sub.user_id)
                output_message += f"  - 媒体标题: {title}\n"
                output_message += f"  - 订阅用户: {user_name}\n"
                output_message += "  ------------------------\n"

            # 4. 将整理好的信息输出到日志
            self.log.info(output_message)
            self.log.info("所有已完成订阅查询结束。")

        except Exception as e:
            self.log.error(f"执行插件时发生未知错误: {e}", exc_info=True)
