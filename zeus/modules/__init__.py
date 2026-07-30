"""Zeus modules — event-driven, independent components.

Each module is a self-contained component that runs independently
and communicates via EventBus events.

Available modules:
  - classifier: Intent classification
  - memory: Session store + facts
  - router: Intent routing
  - pipeline: Plan → Execute → Synthesize
"""

from zeus.module import EventBus, Module, ModuleManager, Event
from zeus.modules.classifier import ClassifierModule
from zeus.modules.memory import MemoryModule
from zeus.modules.router import RouterModule
from zeus.modules.pipeline import PipelineModule