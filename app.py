
import time
from itertools import permutations

import requests
import streamlit as st
import folium
from streamlit_folium import st_folium


# -----------------------------
# PART 1: PAGE SETUP
# -----------------------------

st.set_page_config(
    page_title="Smart Delivery Planner",
    layout="wide"
)

st.title("AI-Based Smart Delivery Route & Task Planner")

st.write(
    "Find real locations, compare delivery orders, "
    "and select a route using distance and delivery deadlines."
)

st.info(
    "Prototype: one warehouse and five deliveries. "
    "Travel times are estimates, not live traffic predictions."
)


# -----------------------------
# PART 2: ADDRESS INPUTS
# -----------------------------

st.subheader("Enter your locations")

warehouse_address = st.text_input(
    "Warehouse / starting address",
    placeholder="Guntur Railway Station, Andhra Pradesh, India"
)

delivery_addresses = []

for i in range(1, 6):
    address = st.text_input(
        f"Delivery location {i}",
        placeholder=(
            "Vignan University, Vadlamudi, Andhra Pradesh, India"
            if i == 1
            else f"Enter delivery address {i}"
        ),
        key=f"address_{i}"
    )
    delivery_addresses.append(address)


# -----------------------------
# PART 3: DELIVERY REQUIREMENTS
# -----------------------------

st.subheader("Delivery requirements")

delivery_rules = {}

for i in range(1, 6):
    col1, col2 = st.columns(2)

    with col1:
        priority = st.selectbox(
            f"Priority for Delivery {i}",
            ["High", "Medium", "Low"],
            index=1,
            key=f"priority_{i}"
        )

    with col2:
        deadline = st.number_input(
            f"Deadline for Delivery {i} (minutes)",
            min_value=5,
            max_value=600,
            value=[60, 90, 120, 150, 180][i - 1],
            step=5,
            key=f"deadline_{i}"
        )

    delivery_rules[f"Delivery {i}"] = {
        "priority": priority,
        "deadline": deadline
    }


# -----------------------------
# PART 4: ADDRESS GEOCODING
# -----------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def find_coordinates(address):
    """Search an address and cache its coordinates for 24 hours."""

    url = "https://nominatim.openstreetmap.org/search"

    response = requests.get(
        url,
        params={
            "q": address.strip(),
            "format": "jsonv2",
            "limit": 1
        },
        headers={
            "User-Agent": "SmartDeliveryPlannerStudentProject/1.0"
        },
        timeout=30
    )

    if response.status_code == 429:
        raise ValueError(
            "The address service has temporarily limited requests. "
            "Please wait before trying again."
        )

    response.raise_for_status()
    results = response.json()

    if not results:
        return None

    return {
        "name": results[0]["display_name"],
        "lat": float(results[0]["lat"]),
        "lon": float(results[0]["lon"])
    }


if st.button("1. Find locations on map", type="primary"):
    addresses = [
        ("Warehouse", warehouse_address)
    ] + [
        (f"Delivery {i}", address)
        for i, address in enumerate(delivery_addresses, start=1)
    ]

    if any(not address.strip() for _, address in addresses):
        st.warning(
            "Enter the warehouse address and all five delivery addresses."
        )

    else:
        found_locations = []
        st.session_state.pop("locations", None)
        st.session_state.pop("planned_route", None)

        try:
            with st.spinner("Finding your locations..."):
                for index, (label, address) in enumerate(addresses):
                    # Keep new public-service requests at least
                    # 1.1 seconds apart. Cached searches need no request.
                    if index > 0:
                        time.sleep(1.1)

                    place = find_coordinates(address)

                    if place is None:
                        raise ValueError(
                            f"Could not find {label}. "
                            "Try a more specific address."
                        )

                    place["label"] = label
                    found_locations.append(place)

                st.session_state["locations"] = found_locations
                st.success("All six locations were found!")

        except ValueError as error:
            st.error(str(error))

        except requests.RequestException as error:
            st.error(
                "The address service could not be reached. "
                "Check your internet connection or wait if requests "
                "have been temporarily limited."
            )
            st.caption(str(error))


# -----------------------------
# PART 5: ROAD DISTANCE MATRIX
# -----------------------------

def get_route_matrix(locations):
    coordinates = ";".join(
        f"{place['lon']},{place['lat']}"
        for place in locations
    )

    url = (
        "https://router.project-osrm.org/table/v1/driving/"
        + coordinates
    )

    response = requests.get(
        url,
        params={"annotations": "distance,duration"},
        timeout=45
    )

    response.raise_for_status()
    data = response.json()

    if (
        data.get("code") != "Ok"
        or not data.get("distances")
        or not data.get("durations")
    ):
        raise ValueError(
            "The routing service could not calculate the distance matrix."
        )

    return {
        "distances": data["distances"],
        "durations": data["durations"]
    }


# -----------------------------
# PART 6: ROAD ROUTE GEOMETRY
# -----------------------------

def get_road_route(ordered_locations):
    coordinates = ";".join(
        f"{place['lon']},{place['lat']}"
        for place in ordered_locations
    )

    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        + coordinates
    )

    response = requests.get(
        url,
        params={
            "overview": "full",
            "geometries": "geojson",
            "steps": "false"
        },
        timeout=45
    )

    response.raise_for_status()
    data = response.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError("Could not obtain the final road route.")

    route = data["routes"][0]

    return {
        "distance_km": route["distance"] / 1000,
        "duration_min": route["duration"] / 60,
        "geometry": route["geometry"]
    }


# -----------------------------
# PART 7: EVALUATE ONE DELIVERY ORDER
# -----------------------------

def evaluate_delivery_order(
    warehouse,
    delivery_order,
    delivery_rules,
    matrix,
    location_index
):
    total_distance_km = 0
    total_driving_minutes = 0
    elapsed_minutes = 0
    delivery_details = []

    full_order = [warehouse] + list(delivery_order)

    for index in range(1, len(full_order)):
        previous_place = full_order[index - 1]
        current_place = full_order[index]

        previous_index = location_index[previous_place["label"]]
        current_index = location_index[current_place["label"]]

        distance_m = matrix["distances"][previous_index][current_index]
        duration_s = matrix["durations"][previous_index][current_index]

        if distance_m is None or duration_s is None:
            raise ValueError(
                "No road route exists between two locations."
            )

        distance_km = distance_m / 1000
        duration_min = duration_s / 60

        total_distance_km += distance_km
        total_driving_minutes += duration_min
        elapsed_minutes += duration_min

        rule = delivery_rules[current_place["label"]]
        arrival_minutes = elapsed_minutes

        late_minutes = max(
            0,
            arrival_minutes - rule["deadline"]
        )

        priority_weight = {
            "High": 3,
            "Medium": 2,
            "Low": 1
        }[rule["priority"]]

        delivery_details.append({
            "delivery": current_place["label"],
            "priority": rule["priority"],
            "deadline_min": rule["deadline"],
            "arrival_min": arrival_minutes,
            "late_min": late_minutes,
            "weighted_penalty": late_minutes * priority_weight
        })

        # Assumption: five minutes to complete each delivery.
        elapsed_minutes += 5

    total_late_min = sum(
        item["late_min"] for item in delivery_details
    )

    weighted_penalty = sum(
        item["weighted_penalty"] for item in delivery_details
    )

    return {
        "distance_km": total_distance_km,
        "duration_min": total_driving_minutes,
        "completion_min": elapsed_minutes,
        "deliveries": delivery_details,
        "total_late_min": total_late_min,
        "weighted_penalty": weighted_penalty
    }


# -----------------------------
# PART 8: COMPARE 120 ORDERS
# -----------------------------

if st.button("2. Compare and plan the best route", type="primary"):
    locations = st.session_state.get("locations", [])

    if len(locations) != 6:
        st.warning("First find all six locations using button 1.")

    else:
        warehouse = locations[0]
        deliveries = locations[1:]

        location_index = {
            place["label"]: index
            for index, place in enumerate(locations)
        }

        route_results = {}

        try:
            with st.spinner(
                "Calculating road distances and comparing 120 orders..."
            ):
                # One matrix request for all six locations.
                matrix = get_route_matrix(locations)

                for order in permutations(deliveries):
                    order_key = tuple(
                        place["label"] for place in order
                    )

                    result = evaluate_delivery_order(
                        warehouse,
                        order,
                        delivery_rules,
                        matrix,
                        location_index
                    )

                    # Project-defined score:
                    # distance in km + 0.5 * weighted late minutes.
                    result["score"] = (
                        result["distance_km"]
                        + 0.5 * result["weighted_penalty"]
                    )

                    route_results[order_key] = result

                baseline_key = tuple(
                    place["label"] for place in deliveries
                )

                best_key = min(
                    route_results,
                    key=lambda key: route_results[key]["score"]
                )

                delivery_by_label = {
                    place["label"]: place
                    for place in deliveries
                }

                best_order = [
                    delivery_by_label[label]
                    for label in best_key
                ]

                # Only one extra request to get the selected road geometry.
                final_route = get_road_route(
                    [warehouse] + best_order
                )

                best_result = route_results[best_key]
                best_result["geometry"] = final_route["geometry"]

                st.session_state["planned_route"] = {
                    "locations": locations,
                    "baseline_key": baseline_key,
                    "best_key": best_key,
                    "baseline": route_results[baseline_key],
                    "best": best_result,
                    "all_results": route_results
                }

            st.success("All 120 delivery orders were evaluated!")

        except (
            requests.RequestException,
            ValueError,
            KeyError,
            TypeError
        ) as error:
            st.session_state.pop("planned_route", None)
            st.error(f"Route planning failed: {error}")


# -----------------------------
# PART 9: LOCATION MAP
# -----------------------------

locations = st.session_state.get("locations", [])

if locations:
    st.subheader("Locations found")

    location_map = folium.Map(
        location=[locations[0]["lat"], locations[0]["lon"]],
        zoom_start=12
    )

    for place in locations:
        folium.Marker(
            [place["lat"], place["lon"]],
            tooltip=place["label"],
            popup=place["name"]
        ).add_to(location_map)

    location_map.fit_bounds([
        [place["lat"], place["lon"]]
        for place in locations
    ])

    st_folium(
        location_map,
        width=900,
        height=450,
        key="location_map"
    )


# -----------------------------
# PART 10: DASHBOARD
# -----------------------------

planned = st.session_state.get("planned_route")

if planned:
    baseline = planned["baseline"]
    best = planned["best"]

    st.subheader("Planner performance dashboard")

    distance_saved = (
        baseline["distance_km"] - best["distance_km"]
    )

    driving_time_change = (
        best["duration_min"] - baseline["duration_min"]
    )

    baseline_late = sum(
        item["late_min"] > 0
        for item in baseline["deliveries"]
    )

    best_late = sum(
        item["late_min"] > 0
        for item in best["deliveries"]
    )

    total_deliveries = len(best["deliveries"])
    deadlines_met = total_deliveries - best_late

    on_time_rate = (
        deadlines_met / total_deliveries * 100
        if total_deliveries else 0
    )

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Distance saved",
        f"{distance_saved:.2f} km"
    )

    col2.metric(
        "Driving-time change",
        f"{driving_time_change:+.1f} min",
        help="Negative means the selected route is faster."
    )

    col3.metric(
        "Late deliveries",
        f"{best_late}/{total_deliveries}",
        delta=f"{best_late - baseline_late} vs baseline",
        delta_color="inverse"
    )

    col4.metric(
        "On-time delivery rate",
        f"{on_time_rate:.1f}%"
    )

    st.subheader("Baseline vs best route")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### Baseline")
        st.write("Order: " + " → ".join(planned["baseline_key"]))
        st.metric("Distance", f"{baseline['distance_km']:.2f} km")
        st.metric("Driving time", f"{baseline['duration_min']:.1f} min")
        st.metric("Combined score", f"{baseline['score']:.2f}")

    with col2:
        st.markdown("### Best route found")
        st.write("Order: " + " → ".join(planned["best_key"]))
        st.metric("Distance", f"{best['distance_km']:.2f} km")
        st.metric("Driving time", f"{best['duration_min']:.1f} min")
        st.metric("Combined score", f"{best['score']:.2f}")

    st.caption(
        "Combined score = distance in km + "
        "0.5 × priority-weighted late minutes. Lower is better."
    )

    # -----------------------------
    # SELECTED ROAD ROUTE MAP
    # -----------------------------

    st.subheader("Selected route on the road map")

    warehouse = planned["locations"][0]

    delivery_by_label = {
        place["label"]: place
        for place in planned["locations"][1:]
    }

    ordered_locations = [warehouse] + [
        delivery_by_label[label]
        for label in planned["best_key"]
    ]

    route_map = folium.Map(
        location=[warehouse["lat"], warehouse["lon"]],
        zoom_start=12
    )

    road_points = [
        [lat, lon]
        for lon, lat in best["geometry"]["coordinates"]
    ]

    folium.PolyLine(
        road_points,
        color="blue",
        weight=6,
        opacity=0.85,
        tooltip="Selected road route"
    ).add_to(route_map)

    for index, place in enumerate(ordered_locations):
        folium.Marker(
            [place["lat"], place["lon"]],
            tooltip=f"{index}: {place['label']}",
            popup=place["name"],
            icon=folium.Icon(
                color="green" if index == 0 else "blue",
                icon="info-sign"
            )
        ).add_to(route_map)

    route_map.fit_bounds(road_points)

    st_folium(
        route_map,
        width=900,
        height=500,
        key="best_route_map"
    )

    # -----------------------------
    # DEADLINE EVALUATION
    # -----------------------------

    st.subheader("Delivery deadline evaluation")

    delivery_rows = []

    for item in best["deliveries"]:
        delivery_rows.append({
            "Delivery": item["delivery"],
            "Priority": item["priority"],
            "Arrival (min)": round(item["arrival_min"], 1),
            "Deadline (min)": item["deadline_min"],
            "Late (min)": round(item["late_min"], 1),
            "Status": (
                "On time"
                if item["late_min"] == 0
                else "Late"
            )
        })

    st.dataframe(
        delivery_rows,
        use_container_width=True,
        hide_index=True
    )

    st.subheader("Performance evaluation")

    st.write(
        f"Possible delivery orders evaluated: "
        f"{len(planned['all_results'])}"
    )

    if baseline["distance_km"] > 0:
        saving_percent = (
            distance_saved / baseline["distance_km"] * 100
        )

        st.write(
            f"Distance change relative to baseline: "
            f"{saving_percent:.2f}%"
        )

    st.caption(
        "Arrival estimates include driving time and five minutes "
        "of service time at each earlier delivery."
    )

    with st.expander("View all route combinations"):
        comparison_rows = []

        for order_key, result in planned["all_results"].items():
            comparison_rows.append({
                "Delivery order": " → ".join(order_key),
                "Distance (km)": round(result["distance_km"], 2),
                "Driving time (min)": round(result["duration_min"], 1),
                "Late deliveries": sum(
                    item["late_min"] > 0
                    for item in result["deliveries"]
                ),
                "Weighted penalty": round(
                    result["weighted_penalty"], 1
                ),
                "Combined score": round(result["score"], 2)
            })

        st.dataframe(
            comparison_rows,
            use_container_width=True,
            hide_index=True
        )


# -----------------------------
# PART 11: ATTRIBUTION
# -----------------------------

st.caption(
    "Map data © OpenStreetMap contributors. "
    "Routing provided by the OSRM public demo service."
)

st.markdown(
    "[OpenStreetMap attribution](https://www.openstreetmap.org/copyright) "
    "| [OSRM API documentation](https://project-osrm.org/docs/v26.5.0/http)"
)
