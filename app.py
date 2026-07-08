"""
Flask API wrapper for the GBP scraper — turns the script into a webhook
endpoint that Zapier/Make can call with a simple HTTP POST, instead of
you having to configure raw Places API calls inside Zapier/Make itself.

Endpoint:
  POST /audit-data
  Body: {"business_name": "Joe's Plumbing", "city_state": "Tampa, FL"}
  Returns: JSON with the trimmed field set (see field list below)

Run locally:
  pip install flask requests
  export GOOGLE_PLACES_API_KEY=your_key_here
  python app.py
  -> runs on http://localhost:5000

Deploy: see deployment steps in the accompanying guide (Render.com, free tier).
"""

import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

# Trimmed field set — only what's actually available via the public API.
DETAIL_FIELD_MASK = ",".join([
    "id",
    "displayName",
    "primaryTypeDisplayName",
    "types",
    "formattedAddress",
    "addressComponents",
    "rating",
    "userRatingCount",
    "editorialSummary",
    "photos",
    "reviews",
    "businessStatus",
    "googleMapsUri",
])


def find_place_id(business_name: str, city_state: str) -> str:
    query = f"{business_name}, {city_state}"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress",
    }
    resp = requests.post(SEARCH_URL, headers=headers, json={"textQuery": query}, timeout=15)
    resp.raise_for_status()
    places = resp.json().get("places", [])
    if not places:
        raise ValueError(f"No place found for query: '{query}'")
    return places[0]["id"]


def get_place_details(place_id: str) -> dict:
    headers = {"X-Goog-Api-Key": API_KEY, "X-Goog-FieldMask": DETAIL_FIELD_MASK}
    resp = requests.get(DETAILS_URL.format(place_id=place_id), headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def extract_city_state(address_components: list) -> str:
    city, state = None, None
    for comp in address_components or []:
        types = comp.get("types", [])
        if "locality" in types:
            city = comp.get("shortText") or comp.get("longText")
        if "administrative_area_level_1" in types:
            state = comp.get("shortText") or comp.get("longText")
    return f"{city}, {state}" if city and state else None


def build_payload(details: dict) -> dict:
    reviews_raw = details.get("reviews", [])[:5]  # API cap, not 10
    reviews_block = [
        {
            "text": (r.get("text", {}) or {}).get("text", ""),
            "rating": r.get("rating"),
            "relative_time": r.get("relativePublishTimeDescription"),
        }
        for r in reviews_raw
    ]

    category = (details.get("primaryTypeDisplayName", {}) or {}).get("text") \
        or (details.get("types", [None]) or [None])[0]

    return {
        "business_name": (details.get("displayName", {}) or {}).get("text"),
        "category": category,
        "city_state": extract_city_state(details.get("addressComponents", []))
                       or details.get("formattedAddress"),
        "rating": details.get("rating"),
        "review_count": details.get("userRatingCount"),
        "google_editorial_description": (details.get("editorialSummary", {}) or {}).get("text"),
        "photo_count": len(details.get("photos", [])),
        "business_status": details.get("businessStatus"),
        "google_maps_url": details.get("googleMapsUri"),
        "reviews_block": reviews_block,
    }


@app.route("/audit-data", methods=["POST"])
def audit_data():
    if not API_KEY:
        return jsonify({"error": "GOOGLE_PLACES_API_KEY not set on server"}), 500

    body = request.get_json(silent=True) or {}
    business_name = body.get("business_name")
    city_state = body.get("city_state")

    if not business_name or not city_state:
        return jsonify({"error": "business_name and city_state are required"}), 400

    try:
        place_id = find_place_id(business_name, city_state)
        details = get_place_details(place_id)
        payload = build_payload(details)
        return jsonify(payload), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except requests.HTTPError as e:
        return jsonify({"error": f"Places API error: {e.response.text}"}), 502


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
