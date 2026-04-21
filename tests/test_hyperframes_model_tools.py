from model_tools import TOOL_TO_TOOLSET_MAP, get_all_tool_names, get_toolset_for_tool


def test_hyperframes_tool_registered_in_model_tools():
    assert "hyperframes_video" in get_all_tool_names()
    assert TOOL_TO_TOOLSET_MAP["hyperframes_video"] == "video"
    assert get_toolset_for_tool("hyperframes_video") == "video"
