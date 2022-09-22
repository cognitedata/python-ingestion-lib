# Cognite Ingestion Library

This is a library for ingesting data with flexible schema into CDF using [jq](https://stedolan.github.io/jq/).

To use, create an instance of Ingester with an optional JQ query, then feed it values and they will be ingested automatically.

```python

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
        ingester.ingest({
            "value": i,
            "time": (datetime.now() + timedelta(seconds=i)).isoformat(),
            "tagName": 'my-test-ts'
        })
        
```

See `example.py` for a working example.

## Development

You will need at least python 3.8 and poetry. Set up with `poetry install`.

Linting is done with `pre-commit`, use `poetry run pre-commit install` to install, or run `poetry run pre-commit run --all` to run all hooks.