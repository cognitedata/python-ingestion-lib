
from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class IngestDatapoint:
    externalId: Optional[str]
    value: Union[str, int, float]
    timestamp: Union[str, int]
