"""Normal operation: a day of plausible INFO logs with a day/night traffic curve."""

import random
from datetime import datetime, timedelta

from incident_agent.data.constants import DAY_START
from incident_agent.data.models import CheckoutRequest, GeneratedData, LogRow
from incident_agent.data.util import CLIENT_VERSIONS, new_request_id

# Share of peak traffic per UTC hour: quiet at night, busy from late morning to evening.
HOURLY_WEIGHT = (
    0.12, 0.10, 0.08, 0.08, 0.10, 0.15,  # 00-05
    0.30, 0.50, 0.70, 0.85, 0.95, 1.00,  # 06-11
    1.00, 0.95, 0.95, 0.90, 0.90, 0.95,  # 12-17
    1.00, 0.95, 0.80, 0.60, 0.40, 0.25,  # 18-23
)  # fmt: skip

# Requests per hour at weight 1.0.
PEAK_REQUESTS = {"checkout": 300, "catalog": 900, "auth": 400}

CACHE_MISS_RATE = 0.03


def hour_timestamps(rng: random.Random, hour: int, count: int) -> list[datetime]:
    start = DAY_START + timedelta(hours=hour)
    seconds = sorted(rng.randrange(3600) for _ in range(count))
    return [start + timedelta(seconds=s) for s in seconds]


def requests_in_hour(rng: random.Random, service: str, hour: int) -> int:
    return round(PEAK_REQUESTS[service] * HOURLY_WEIGHT[hour] * rng.uniform(0.9, 1.1))


def generate_normal_operation(rng: random.Random) -> GeneratedData:
    data = GeneratedData()
    for hour in range(24):
        for ts in hour_timestamps(rng, hour, requests_in_hour(rng, "checkout", hour)):
            _checkout_request(rng, data, ts)
        for ts in hour_timestamps(rng, hour, requests_in_hour(rng, "catalog", hour)):
            _catalog_request(rng, data, ts)
        for ts in hour_timestamps(rng, hour, requests_in_hour(rng, "auth", hour)):
            _auth_request(rng, data, ts)
    return data


def _checkout_request(rng: random.Random, data: GeneratedData, ts: datetime) -> None:
    request_id = new_request_id(rng)
    data.checkout_requests.append(CheckoutRequest(ts, request_id, rng.choice(CLIENT_VERSIONS)))
    latency = rng.randint(120, 480)
    amount = rng.randint(500, 25000) / 100
    order = rng.randint(100000, 999999)
    checkout_message = f"POST /checkout 200 {latency}ms"
    payment_message = f"charge ok order=ord-{order} amount={amount:.2f} EUR"
    data.logs.append(LogRow(ts, "checkout", "INFO", checkout_message, request_id))
    data.logs.append(LogRow(ts, "payment", "INFO", payment_message, request_id))


def _catalog_request(rng: random.Random, data: GeneratedData, ts: datetime) -> None:
    request_id = new_request_id(rng)
    product = rng.randint(1000, 9999)
    if rng.random() < CACHE_MISS_RATE:
        message = f"cache miss for key product:{product}, loading from database"
        data.logs.append(LogRow(ts, "catalog", "WARN", message, request_id))
    else:
        message = f"GET /products/{product} 200 {rng.randint(8, 90)}ms"
        data.logs.append(LogRow(ts, "catalog", "INFO", message, request_id))


def _auth_request(rng: random.Random, data: GeneratedData, ts: datetime) -> None:
    request_id = new_request_id(rng)
    message = f"token validated for session s-{rng.getrandbits(32):08x}"
    data.logs.append(LogRow(ts, "auth", "INFO", message, request_id))
