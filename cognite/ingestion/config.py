from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class ExternalId:
    external_id: str


@dataclass
class InternalId:
    id: int


@dataclass
class DataPointsDestination:
    data_set: Optional[Union[ExternalId, InternalId]]
    external_id: Optional[str]
    max_queue_size: Optional[int]
    max_upload_interval: Optional[int]
