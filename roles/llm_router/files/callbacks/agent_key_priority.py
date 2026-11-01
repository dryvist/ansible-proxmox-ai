"""Pin the LiteLLM scheduler priority declared for agent keys."""

from litellm.integrations.custom_logger import CustomLogger


class AgentKeyPriority(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        """Pin roster-agent requests to their configured priority; a client value can't lower it."""
        if not isinstance(data, dict):
            return data

        metadata = getattr(user_api_key_dict, "metadata", None)
        if not isinstance(metadata, dict) or metadata.get("router_key_class") != "agent":
            return data

        priority = metadata.get("router_request_priority")
        if type(priority) is int:
            data["priority"] = priority
        return data


proxy_handler_instance = AgentKeyPriority()
