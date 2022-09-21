from datetime import datetime, timedelta
import os
from cognite.ingestion import Ingester
from cognite.ingestion.config import DataPointsDestination
from cognite.client import CogniteClient, ClientConfig
from cognite.client.credentials import OAuthClientCredentials

client = CogniteClient(
    ClientConfig(
        client_name="ingester-test",
        project=os.environ["PROJECT"],
        base_url=os.environ["BASE_URL"],
        credentials=OAuthClientCredentials(
            token_url=os.environ["TOKEN_URL"],
            client_id=os.environ["CLIENT_ID"],
            client_secret=os.environ["CLIENT_SECRET"],
            scopes=[f"{(os.environ['BASE_URL'])}/.default"]
        )
    )
)

with Ingester(
    'MyIngester',
    '{"value":.value, "timestamp":.time, "externalId":.tagName}',
    cdf_client=client,
    config=DataPointsDestination(
        max_queue_size=10_000,
        max_upload_interval=1,
        data_set=None,
        external_id=None
    )
) as ingester:
    for i in range(100_000):
        data = {
            "value": i,
            "time": (datetime.now() + timedelta(seconds=i)).isoformat(),
            "tagName": 'my-test-ts'
        }
        ingester.ingest(data)