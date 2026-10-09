"""Controlled Python outputs for the actual TypeScript guard (no HTTP/config)."""

import json
from datetime import datetime, timezone

from app.integrations.amap_weather import convert_forecast
from app.schemas.weather import NON_PRECIPITATION, RAIN, RAIN_SNOW, SNOW, WeatherRequest


def cases() -> list[dict[str, object]]:
    now = datetime(2026, 12, 31, 16, 0, tzinfo=timezone.utc)
    request = WeatherRequest(start_date="2026-12-31", end_date="2027-01-02")
    outputs = []
    descriptions = sorted(RAIN | RAIN_SNOW | SNOW | NON_PRECIPITATION) + ["未识别的新天气", None]
    for description in descriptions:
        for reporttime in ("2027-01-01 00:00:00", "2026-12-30 23:59:59", None, "invalid", "2099-01-01 00:00:00"):
            # Deliberately unordered, one missing date; day/night retain their
            # different semantics and include zero, negative and unknown values.
            payload = {"status": "1", "forecasts": [{
                "city": "上海市", "adcode": "310000", "reporttime": reporttime,
                "casts": [
                    {"date": "2027-01-02", "dayweather": description, "daytemp": "0", "nighttemp": "-2.5", "daypower": "1-3"},
                    {"date": "2026-12-31", "nightweather": description, "daywind": "东", "nightpower": []},
                ],
            }]}
            outputs.append(convert_forecast(payload, request, now).model_dump(mode="json"))
    for start, end in (("2026-02-28", "2026-03-01"), ("2027-01-01", "2027-01-01")):
        empty_request = WeatherRequest(start_date=start, end_date=end)
        outputs.append(convert_forecast({"status": "1", "forecasts": []}, empty_request, now).model_dump(mode="json"))
    outputs.append(convert_forecast({"status": "1", "forecasts": [{
        "city": "上海市", "adcode": "310000", "reporttime": "2026-12-31 00:00:00", "casts": [],
    }]}, request, now.replace(microsecond=1)).model_dump(mode="json"))
    return outputs


if __name__ == "__main__":
    print(json.dumps(cases(), ensure_ascii=False))
