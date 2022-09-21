import threading
from typing import Any, Callable, Dict, Optional, Union

import pyjq
from cognite.client import CogniteClient

from .config import DataPointsDestination, ExternalId, InternalId
from .queues import TimeSeriesUploadQueue


class Ingester:
    def __init__(
        self,
        name: str,
        query: Optional[str],
        cdf_client: CogniteClient,
        config: DataPointsDestination,
        log_callback: Optional[Callable[[int, str], None]] = None,
        metrics_callback: Optional[Callable[[str, Dict[str, str], float], None]] = None,
        cancelation_token: threading.Event = threading.Event(),
    ):
        self.cdf_client = cdf_client

        self.log_callback = log_callback
        self.metrics_callback = metrics_callback

        self.cancelation_token = cancelation_token
        if query is not None:
            self.transformation = pyjq.compile(query)

        if isinstance(config, DataPointsDestination):
            data_set_id = self._get_data_set_id(config.data_set)
            self.upload_queue = TimeSeriesUploadQueue(
                cdf_client=self.cdf_client,
                max_queue_size=config.max_queue_size,
                max_upload_interval=config.max_upload_interval,
                thread_name=f"UploadQueue_{name}",
                log_callback=self.log_callback,
                metrics_callback=self.metrics_callback,
                data_set_id=data_set_id,
                default_external_id=config.external_id,
                cancelation_token=self.cancelation_token,
            )

    def ingest(self, input: Any):
        if self.transformation is not None:
            input = self.transformation.all(input)
        self.upload_queue.add_to_upload_queue(input)

    def _get_data_set_id(self, id: Optional[Union[InternalId, ExternalId]]) -> Optional[int]:
        if id is None:
            return None
        if isinstance(id, InternalId):
            return id.id
        else:
            return self.cdf_client.data_sets.retrieve(external_id=id.external_id).id

    def __enter__(self) -> "Ingester":
        """
        Wraps around start method, for use as context manager
        Returns:
            self
        """
        self.upload_queue.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Wraps around stop method, for use as context manager
        Args:
            exc_type: Exception type
            exc_val: Exception value
            exc_tb: Traceback
        """
        self.upload_queue.__exit__(exc_type, exc_val, exc_tb)
