from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
import math
import threading
from typing import Any, Callable, Dict, List, Optional, Union
from cognite.client import CogniteClient
from cognite.client.data_classes.time_series import TimeSeries
from cognite.client.exceptions import CogniteAPIError, CogniteDuplicatedError, CogniteNotFoundError, CogniteReadTimeout
from .retry import retry
from .data_types import IngestDatapoint
from dateutil.parser import parse

def _resolve_log_level(level: str) -> int:
    return {"NOTSET": 0, "DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}[level.upper()]

RETRY_BACKOFF_FACTOR = 1.5
RETRY_MAX_DELAY = 15
RETRY_DELAY = 5
RETRIES = 10

class AbstractUploadQueue(ABC):
    """
    Abstract uploader class.
    Args:
        cdf_client: Cognite Data Fusion client to use
        post_upload_function: A function that will be called after each upload. The function will be given one argument:
            A list of the elements that were uploaded.
        max_queue_size: Maximum size of upload queue. Defaults to no max size.
        max_upload_interval: Automatically trigger an upload each m seconds when run as a thread (use start/stop
            methods).
        trigger_log_level: Log level to log upload triggers to.
        thread_name: Thread name of uploader thread.
    """

    def __init__(
        self,
        cdf_client: CogniteClient,
        max_queue_size: Optional[int] = None,
        max_upload_interval: Optional[int] = None,
        thread_name: Optional[str] = None,
        log_callback: Optional[Callable[[int, str], None]] = None,
        metrics_callback: Optional[Callable[[str, Dict[str, str], float], None]] = None,
        cancelation_token: threading.Event = threading.Event(),
    ):
        self.cdf_client = cdf_client

        self.threshold = max_queue_size if max_queue_size is not None else -1
        self.upload_queue_size = 0

        self.thread = threading.Thread(target=self._run, daemon=True, name=thread_name)
        self.lock = threading.RLock()
        self.cancelation_token: threading.Event = cancelation_token

        self.max_upload_interval = max_upload_interval

        self.log_callback = log_callback
        self.metrics_callback = metrics_callback
        self.log_level = _resolve_log_level("DEBUG")
        self.error_level = _resolve_log_level("ERROR")

    def _check_triggers(self) -> None:
        """
        Check if upload triggers are met, call upload if they are. Called by subclasses.
        """
        if self.upload_queue_size > self.threshold >= 0:
            self.log_callback(self.log_level, f"Upload queue reached threshold size {self.upload_queue_size}/{self.threshold}, triggering upload",)
            return self.upload()

        return None

    def _post_upload(self, uploaded: List[Any]) -> None:
        """
        Perform post_upload_function to uploaded data, if applicable
        Args:
            uploaded: List of uploaded data
        """

    @abstractmethod
    def add_to_upload_queue(self, *args) -> None:
        """
        Adds an element to the upload queue. The queue will be uploaded if the queue byte size is larger than the
        threshold specified in the config.
        """

    @abstractmethod
    def upload(self) -> None:
        """
        Uploads the queue.
        """

    def _run(self) -> None:
        """
        Internal run method for upload thread
        """
        while not self.cancelation_token.wait(timeout=self.max_upload_interval):
            try:
                self.log_callback(self.log_level, "Triggering scheduled upload")
                self.upload()
            except Exception as e:
                self.log_callback(self.error_level, f"Unexpected error while uploading: {str(e)}. Skipping this upload.")

        # trigger stop event explicitly to drain the queue
        self.stop(ensure_upload=True)

    def start(self) -> None:
        """
        Start upload thread if max_upload_interval is set, this called the upload method every max_upload_interval
        seconds.
        """
        if self.max_upload_interval is not None:
            self.cancelation_token.clear()
            self.thread.start()

    def stop(self, ensure_upload: bool = True) -> None:
        """
        Stop upload thread if running, and ensures that the upload queue is empty if ensure_upload is True.
        Args:
            ensure_upload (bool): (Optional). Call upload one last time after shutting down thread to ensure empty
                upload queue.
        """
        self.cancelation_token.set()
        if ensure_upload:
            self.upload()

class DataPoint:
    value: Union[int, float]
    timestamp: datetime

MIN_DATAPOINT_TIMESTAMP = 31536000000
MAX_DATAPOINT_STRING_LENGTH = 255
MAX_DATAPOINT_VALUE = 1e100
MIN_DATAPOINT_VALUE = -1e100

class TimeSeriesUploadQueue(AbstractUploadQueue):
    """
    Upload queue for time series
    Args:
        cdf_client: Cognite Data Fusion client to use
        post_upload_function: A function that will be called after each upload. The function will be given one argument:
            A list of dicts containing the datapoints that were uploaded (on the same format as the kwargs in
            datapoints upload in the Cognite SDK).
        max_queue_size: Maximum size of upload queue. Defaults to no max size.
        max_upload_interval: Automatically trigger an upload each m seconds when run as a thread (use start/stop
            methods).
        trigger_log_level: Log level to log upload triggers to.
        thread_name: Thread name of uploader thread.
        create_missing: Create missing time series if possible (ie, if external id is used). Either given as a boolean
            (True would auto-create a time series with nothing but an external ID), or as a factory function taking an
            external ID and a list of datapoints about to be inserted and returning a TimeSeries object.
    """

    def __init__(
        self,
        cdf_client: CogniteClient,
        max_queue_size: Optional[int] = None,
        max_upload_interval: Optional[int] = None,
        thread_name: Optional[str] = None,
        log_callback: Optional[Callable[[int, str], None]] = None,
        metrics_callback: Optional[Callable[[str, Dict[str, str], float], None]] = None,
        data_set_id: Optional[int] = None,
        default_external_id: Optional[str] = None,
        cancelation_token: threading.Event = threading.Event(),
    ):
        # Super sets post_upload and threshold
        super().__init__(
            cdf_client,
            max_queue_size,
            max_upload_interval,
            thread_name,
            log_callback,
            metrics_callback,
            cancelation_token,
        )

        self.upload_queue: Dict[str, List[DataPoint]] = dict()
        self.data_set_id = data_set_id
        self.default_external_id = default_external_id

    def _verify_datapoint_time(self, time: Union[int, float, datetime]) -> bool:
        if isinstance(time, int) or isinstance(time, float):
            return not math.isnan(time) and time >= MIN_DATAPOINT_TIMESTAMP
        else:
            return time.timestamp() * 1000.0 >= MIN_DATAPOINT_TIMESTAMP

    def _verify_datapoint_value(self, value: Union[int, float, str]) -> bool:
        if isinstance(value, float):
            return not (
                math.isnan(value) or math.isinf(value) or value > MAX_DATAPOINT_VALUE or value < MIN_DATAPOINT_VALUE
            )
        elif isinstance(value, str):
            return len(value) <= MAX_DATAPOINT_STRING_LENGTH
        else:
            return True

    def _ts_factory(self, external_id: str, datapoints: List[DataPoint]) -> TimeSeries:
        """
        Create a time series from external id and list of datapoints
        Args:
            external_id: External ID of time series to create
            datapoints: The list of datapoints that were tried to be inserted
        Returns:
            A TimeSeries object with external_id set, and the is_string automatically detected
        """
        is_string = (
            isinstance(datapoints[0].get("value"), str)
            if isinstance(datapoints[0], dict)
            else isinstance(datapoints[0][1], str)
        )
        return TimeSeries(external_id=external_id, is_string=is_string, data_set_id=self.data_set_id)

    def _is_datapoint_valid(
        self,
        dp: DataPoint,
    ) -> bool:
        return self._verify_datapoint_time(dp.timestamp) and self._verify_datapoint_value(dp.value)

    def add_to_upload_queue(self, datapoints: Union[IngestDatapoint, List[IngestDatapoint]]) -> None:
        """
        Add data points to upload queue. The queue will be uploaded if the queue size is larger than the threshold
        specified in the __init__.
        Args:
            id: Internal ID of time series. Either this or external_id must be set.
            external_id: External ID of time series. Either this or external_id must be set.
            datapoints: List of data points to add
        """
        old_len = len(datapoints)
        new_len = 0
        with self.lock:
            if not isinstance(datapoints, list):
                datapoints=[datapoints]
            for dp in datapoints:
                if dp.externalId is None:
                    dp.externalId = self.default_external_id
                if dp.externalId is None:
                    continue

                timestamp = dp.timestamp
                if isinstance(timestamp, str):
                    timestamp = parse(timestamp, fuzzy=True).timestamp() * 1000.0

                datapoint = DataPoint(timestamp=timestamp, value=dp.value)

                if not self._is_datapoint_valid(datapoint):
                    continue

                if dp.externalId not in self.upload_queue:
                    new_len += 1
                    self.upload_queue[dp.externalId] = [datapoint]
                else:
                    self.upload_queue[dp.externalId].append(datapoint)

            self._check_triggers()

        if old_len > new_len:
            diff = old_len - new_len
            self.log_callback(self.log_level, f"Discarding {diff} datapoints due to bad timestamp or value")

    def upload(self) -> None:
        """
        Trigger an upload of the queue, clears queue afterwards
        """
        if len(self.upload_queue) == 0:
            return

        with self.lock:
            upload_this = self._upload_batch(
                [
                    {"externalId": extid, "datapoints": datapoints}
                    for extid, datapoints in self.upload_queue.items()
                    if len(datapoints) > 0
                ]
            )

            try:
                self._post_upload(upload_this)
            except Exception as e:
                self.log_callback(self.log_level, f"Error in upload callback: {str(e)}")

            self.upload_queue.clear()
            self.log_callback(self.log_level, f"Uploaded {self.upload_queue_size} datapoints")
            self.upload_queue_size = 0

    @retry(
        exceptions=(CogniteAPIError, ConnectionError),
        tries=RETRIES,
        delay=RETRY_DELAY,
        max_delay=RETRY_MAX_DELAY,
        backoff=RETRY_BACKOFF_FACTOR,
    )
    def _upload_batch(self, upload_this: List[Dict], retries=5) -> List[Dict]:
        if len(upload_this) == 0:
            return upload_this

        try:
            self.cdf_client.datapoints.insert_multiple(upload_this)

        except CogniteNotFoundError as ex:
            if not retries:
                raise ex

            # Get IDs of time series that exists, but failed because of the non-existing time series
            retry_these = [id_dict["externalId"] for id_dict in ex.failed if id_dict not in ex.not_found and "externalId" in id_dict]

            # Get the time series that can be created
            create_these_ids = [id_dict["externalId"] for id_dict in ex.not_found if "externalId" in id_dict]
            datapoints_lists: Dict[str, List[DataPoint]] = {
                ts_dict["externalId"]: ts_dict["datapoints"]
                for ts_dict in upload_this
                if ts_dict["externalId"] in create_these_ids
            }

            self.log_callback(self.log_level, f"Creating {len(create_these_ids)} time series")
            self.cdf_client.time_series.create(
                [
                    self._ts_factory(external_id, datapoints_lists[external_id])
                    for external_id in create_these_ids
                ]
            )

            retry_these.extend([i for i in create_these_ids])

            if len(ex.not_found) != len(create_these_ids):
                missing = [id_dict for id_dict in ex.not_found if id_dict.get("externalId") not in retry_these]
                self.log_callback(
                    self.log_level,
                    f"{len(ex.not_found) - len(create_these_ids)} time series not found, and could not be created automatically:\n"
                    + str(missing)
                    + "\nData will be dropped"
                )

            # Remove entries with non-existing time series from upload queue
            upload_this = [
                entry
                for entry in upload_this
                if entry.get("externalId") in retry_these
            ]

            # Upload remaining
            self._upload_batch(upload_this, retries - 1)

        return upload_this

    def __enter__(self) -> "TimeSeriesUploadQueue":
        """
        Wraps around start method, for use as context manager
        Returns:
            self
        """
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Wraps around stop method, for use as context manager
        Args:
            exc_type: Exception type
            exc_val: Exception value
            exc_tb: Traceback
        """
        self.stop()

    def __len__(self) -> int:
        """
        The size of the upload queue
        Returns:
            Number of data points in queue
        """
        return self.upload_queue_size
