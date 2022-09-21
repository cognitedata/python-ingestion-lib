from typing import Optional, Union


class ExternalId:
    external_id: str


class InternalId:
    id: int


class DataPointsDestination:
    data_set: Optional[Union[ExternalId, InternalId]]
    external_id: Optional[str]
    max_queue_size: Optional[int]
    max_upload_interval: Optional[int]
