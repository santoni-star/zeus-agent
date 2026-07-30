"""Zeus modules — event-driven, independent components.

Each module is a self-contained component that runs independently
and communicates via EventBus events.

Available modules:
  - classifier: Intent classification
  - memory: Session store + facts
  - router: Intent routing
  - pipeline: Plan -> Execute -> Synthesize
  - reflection: Task pattern analysis and auto-tool creation
  - gateway: Telegram bridge (optional)
  - scheduler: Cron scheduling (optional)
"""

from zeus.module import EventBus, Module, ModuleManager, Event
from zeus.modules.classifier import ClassifierModule
from zeus.modules.memory import MemoryModule
from zeus.modules.router import RouterModule
from zeus.modules.pipeline import PipelineModule
from zeus.modules.reflection import ReflectionModule
from zeus.modules.sub_agent import SubAgentManager
from zeus.modules.mcp import MCPModule
from zeus.modules.gateway import GatewayModule
from zeus.modules.scheduler import SchedulerModule