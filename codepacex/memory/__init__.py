"""组织 codepacex.memory 包的公开接口与子模块。"""

from codepacex.memory.auto_memory import (
    ENTRYPOINT_NAME,
    MemoryFile,
    MemoryManager,
    build_memory_prompt,
    ensure_memory_dir_exists,
    get_auto_mem_path,
    get_user_auto_mem_path,
    is_auto_mem_path,
    parse_frontmatter,
)
from codepacex.memory.instructions import load_instructions, process_includes
from codepacex.memory.consolidation import MemoryConsolidator
from codepacex.memory.recall import (
    RelevantMemory,
    find_relevant_memories,
    render_reminder,
)
from codepacex.memory.session import (
    ResumeResult,
    Session,
    SessionManager,
    SessionMeta,
    SessionRecord,
    generate_session_summary,
    make_compact_boundary,
    parse_compact_boundary,
    validate_message_chain,
)


__all__ = [
    "ENTRYPOINT_NAME",
    "MemoryFile",
    "MemoryManager",
    "MemoryConsolidator",
    "RelevantMemory",
    "ResumeResult",
    "Session",
    "SessionManager",
    "SessionMeta",
    "SessionRecord",
    "build_memory_prompt",
    "ensure_memory_dir_exists",
    "find_relevant_memories",
    "generate_session_summary",
    "get_auto_mem_path",
    "get_user_auto_mem_path",
    "is_auto_mem_path",
    "load_instructions",
    "make_compact_boundary",
    "parse_compact_boundary",
    "parse_frontmatter",
    "process_includes",
    "render_reminder",
    "validate_message_chain",
]
