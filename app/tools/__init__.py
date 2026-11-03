"""Explicit AI tool registration; unknown files are never loaded automatically."""
from .memory_tools import ENTRIES as MEMORY_ENTRIES
from .notepad_tools import ENTRIES as NOTEPAD_ENTRIES
from .entry_tools import ENTRIES as ENTRY_ENTRIES
from .world_tools import ENTRIES as WORLD_ENTRIES
from .lifestyle_tools import ENTRIES as LIFESTYLE_ENTRIES
from .search_tools import ENTRIES as SEARCH_ENTRIES
from .document_updates import ENTRIES as DOCUMENT_UPDATES
from .working_updates import ENTRIES as WORKING_UPDATES
from .location_updates import ENTRIES as LOCATION_UPDATES
from .lifestyle_updates import ENTRIES as LIFESTYLE_UPDATES
from .schedule_tools import ENTRIES as SCHEDULE_ENTRIES
from .external_tools import ENTRIES as EXTERNAL_ENTRIES
from .music_tools import ENTRIES as MUSIC_ENTRIES
from .web_tools import ENTRIES as WEB_ENTRIES
from .image_tools import ENTRIES as IMAGE_ENTRIES
from .autonomy_tools import ENTRIES as AUTONOMY_ENTRIES
from .action_reservations import ENTRIES as ACTION_ENTRIES
from .core_reflection import ENTRIES as CORE_REFLECTION_ENTRIES
from .diary_archives import ENTRIES as DIARY_ARCHIVE_ENTRIES
from .persona_contracts import ENTRIES as PERSONA_ENTRIES
from .registry import ToolRegistry
from .capability_tools import ENTRIES as CAPABILITY_ENTRIES
from .timeline_tools import ENTRIES as TIMELINE_ENTRIES
from .procedure_tools import ENTRIES as PROCEDURE_ENTRIES
from .purpose_tools import ENTRIES as PURPOSE_ENTRIES
from .research_tools import ENTRIES as RESEARCH_ENTRIES
from .subscription_tools import ENTRIES as SUBSCRIPTION_ENTRIES
from .watchlist_tools import ENTRIES as WATCHLIST_ENTRIES
from .inbox_tools import ENTRIES as INBOX_ENTRIES
from .entity_tools import ENTRIES as ENTITY_ENTRIES
from .shared_procedures import ENTRIES as SHARED_PROCEDURE_ENTRIES
from .approval_tools import ENTRIES as APPROVAL_ENTRIES
from .question_progress import ENTRIES as QUESTION_ENTRIES
from .guidance import documented_specs

REGISTRY=ToolRegistry(documented_specs([*EXTERNAL_ENTRIES,*MUSIC_ENTRIES,*PERSONA_ENTRIES,*DIARY_ARCHIVE_ENTRIES,*CORE_REFLECTION_ENTRIES,*ACTION_ENTRIES,*CAPABILITY_ENTRIES,*APPROVAL_ENTRIES,*QUESTION_ENTRIES,*MEMORY_ENTRIES,*NOTEPAD_ENTRIES,*ENTRY_ENTRIES,*WORLD_ENTRIES,*LIFESTYLE_ENTRIES,*SEARCH_ENTRIES,*DOCUMENT_UPDATES,*WORKING_UPDATES,*LOCATION_UPDATES,*LIFESTYLE_UPDATES,*SCHEDULE_ENTRIES,*WEB_ENTRIES,*IMAGE_ENTRIES,*AUTONOMY_ENTRIES,*TIMELINE_ENTRIES,*PROCEDURE_ENTRIES,*SHARED_PROCEDURE_ENTRIES,*PURPOSE_ENTRIES,*RESEARCH_ENTRIES,*SUBSCRIPTION_ENTRIES,*WATCHLIST_ENTRIES,*INBOX_ENTRIES,*ENTITY_ENTRIES]))
# Compatibility exports for internal callers; live conversations use definitions(context).
TOOLS=REGISTRY.definitions()
HANDLERS={entry['name']:REGISTRY.get(entry['name']).handler for entry in REGISTRY.catalog()}
