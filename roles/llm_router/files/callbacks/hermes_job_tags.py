"""Map authenticated Hermes request metadata to bounded Prometheus tags."""

import base64
import json
import os
import re

from litellm.integrations.custom_logger import CustomLogger

_TAG_RE = re.compile(r"^telemetry_job_[a-z0-9_]+$")


def _load_job_tags():
    encoded = os.environ.get("LLM_ROUTER_HERMES_JOB_TAG_MAP_B64", "")
    if not encoded:
        return {}, False
    try:
        mapping = json.loads(base64.b64decode(encoded, validate=True))
        if not isinstance(mapping, dict):
            return {}, False
        if any(
            not isinstance(name, str)
            or not isinstance(tag, str)
            or not _TAG_RE.fullmatch(tag)
            for name, tag in mapping.items()
        ):
            return {}, False
        return mapping, True
    except Exception:
        return {}, False


_JOB_TAGS, _JOB_TAGS_CONFIGURED = _load_job_tags()


def _apply_job_tag(user_api_key_dict, data):
    """Return a copied request with one bounded tag, or the input unchanged."""
    try:
        if not isinstance(data, dict) or not _JOB_TAGS_CONFIGURED:
            return data
        key_alias = getattr(user_api_key_dict, "key_alias", None)
        if not isinstance(key_alias, str) or not key_alias.startswith("hermes-"):
            return data

        metadata_key = "metadata"
        if metadata_key not in data:
            metadata = {}
        else:
            metadata = data[metadata_key]
        if not isinstance(metadata, dict):
            return data

        job_name = metadata.get("hermes_job")
        if job_name is not None and not isinstance(job_name, str):
            return data

        tags = metadata.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            return data

        if job_name:
            tag = _JOB_TAGS.get(job_name, "telemetry_job_other")
        else:
            tag = "telemetry_job_aux"

        updated_metadata = dict(metadata)
        updated_tags = list(tags)
        if tag not in updated_tags:
            updated_tags.append(tag)
        updated_metadata["tags"] = updated_tags
        updated_data = dict(data)
        updated_data[metadata_key] = updated_metadata
        return updated_data
    except Exception:
        # Attribution must never be able to reject or alter a model request.
        return data


class HermesJobTags(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type: str):
        return _apply_job_tag(user_api_key_dict, data)


proxy_handler_instance = HermesJobTags()
