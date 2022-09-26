import os
from datetime import datetime, timedelta

from cognite.client import ClientConfig, CogniteClient
from cognite.client.credentials import OAuthClientCredentials

from cognite.ingestion import Ingester
from cognite.ingestion.config import DataPointsDestination

client = CogniteClient(
    ClientConfig(
        client_name="ingester-test",
        project=os.environ["PROJECT"],
        base_url=os.environ["BASE_URL"],
        credentials=OAuthClientCredentials(
            token_url=os.environ["TOKEN_URL"],
            client_id=os.environ["CLIENT_ID"],
            client_secret=os.environ["CLIENT_SECRET"],
            scopes=[f"{(os.environ['BASE_URL'])}/.default"],
        ),
    )
)

with Ingester(
    "MyIngester",
    '. as $orig | .values[] as $arr | {"value": $arr.value, "timestamp": $arr.time, "externalId": $orig.tagName}',
    cdf_client=client,
    config=DataPointsDestination(max_queue_size=10_000, max_upload_interval=1, data_set=None, external_id=None),
) as ingester:
    for i in range(100_000):
        data = {
            "values": [
                {"value": i * 2, "time": (datetime.now() + timedelta(seconds=i * 2)).isoformat()},
                {"value": i * 2 + 1, "time": (datetime.now() + timedelta(seconds=i * 2 + 1)).isoformat()},
            ],
            "tagName": "my-test-ts",
        }
        ingester.ingest(data)
