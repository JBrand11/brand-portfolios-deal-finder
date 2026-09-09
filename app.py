
import streamlit as st
import pandas as pd
import numpy as np
import requests
import re
import math
from urllib.parse import quote
from datetime import date, datetime

st.set_page_config(page_title="Brand Portfolios Deal Finder", page_icon="🏠", layout="wide")

HMLR_ENDPOINT = "https://landregistry.data.gov.uk/landregistry/query"
POSTCODES_IO = "https://api.postcodes.io"

PROPERTY_TYPES = {
    "http://landregistry.data.gov.uk/def/common/detached": "Detached",
    "http://landregistry.data.gov.uk/def/common/semi-detached": "Semi-detached",
    "http://landregistry.data.gov.uk/def/common/terraced": "Terraced",
    "http://landregistry.data.gov.uk/def/common/flat-maisonette": "Flat/Maisonette",
    "http://landregistry.data.gov.uk/def/common/otherPropertyType": "Other",
}

st.markdown("""
<style>
.big-score {
    font-size: 36px;
    font-weight: 700;
    line-height: 1;
}
.small-muted { color: #6b7280; font-size: 0.9rem; }
.card {
    border: 1px solid rgba(128,128,128,.25);
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 10px;
}
</style>
""", unsafe_allow_html=True)

def money(v):
    try:
        return f"£{float(v):,.0f}"
    except:
        return "—"

def normalise_postcode(pc):
    pc = re.sub(r"\s+", "", str(pc).upper())
    if len(pc) < 5:
        return pc
    return pc[:-3] + " " + pc[-3:]

def extract_postcode(text):
    pattern = r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b"
    m = re.search(pattern, text.upper())
    return normalise_postcode(m.group(1)) if m else None

def parse_subject_address(text):
    postcode = extract_postcode(text)
    before = text
    if postcode:
        compact_pc = re.sub(r"\s+", "", postcode)
        # remove postcode flexibly
        before = re.sub(re.escape(postcode), "", before, flags=re.I)
        before = re.sub(re.escape(compact_pc), "", before, flags=re.I)
    bits = [x.strip(" ,") for x in before.split(",") if x.strip(" ,")]
    first = bits[0] if bits else before.strip()
    m = re.match(r"^\s*([0-9]+[A-Za-z]?)\s+(.+)$", first)
    if m:
        paon = m.group(1).upper()
        street = m.group(2).strip().upper()
    else:
        # Named houses: first component is PAON; street may be the second component.
        paon = first.upper()
        street = bits[1].upper() if len(bits) > 1 else ""
    return {"postcode": postcode, "paon": paon, "street": street, "raw": text}

def hmlr_query_for_postcodes(postcodes):
    postcodes = [normalise_postcode(p) for p in postcodes if p]
    if not postcodes:
        return pd.DataFrame()

    value_string = " ".join(f'"{p}"^^xsd:string' for p in postcodes[:25])

    query = f"""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX lrppi: <http://landregistry.data.gov.uk/def/ppi/>
PREFIX lrcommon: <http://landregistry.data.gov.uk/def/common/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT ?paon ?saon ?street ?town ?county ?postcode ?amount ?date ?category ?estateType ?propertyType
WHERE {{
  VALUES ?postcode {{ {value_string} }}

  ?addr lrcommon:postcode ?postcode.

  ?transx lrppi:propertyAddress ?addr ;
          lrppi:pricePaid ?amount ;
          lrppi:transactionDate ?date ;
          lrppi:estateType ?estateType ;
          lrppi:propertyType ?propertyType ;
          lrppi:transactionCategory/skos:prefLabel ?category.

  OPTIONAL {{?addr lrcommon:county ?county}}
  OPTIONAL {{?addr lrcommon:paon ?paon}}
  OPTIONAL {{?addr lrcommon:saon ?saon}}
  OPTIONAL {{?addr lrcommon:street ?street}}
  OPTIONAL {{?addr lrcommon:town ?town}}
}}
ORDER BY DESC(?date)
LIMIT 1500
"""
    headers = {"Accept": "application/sparql-results+json", "User-Agent": "BrandPortfoliosDealFinder/0.2"}
    r = requests.get(HMLR_ENDPOINT, params={"query": query}, headers=headers, timeout=45)
    r.raise_for_status()
    data = r.json()

    rows = []
    for binding in data.get("results", {}).get("bindings", []):
        def val(k):
            return binding.get(k, {}).get("value")
        rows.append({
            "paon": val("paon") or "",
            "saon": val("saon") or "",
            "street": val("street") or "",
            "town": val("town") or "",
            "county": val("county") or "",
            "postcode": normalise_postcode(val("postcode") or ""),
            "price": float(val("amount")) if val("amount") else np.nan,
            "date": pd.to_datetime(val("date"), errors="coerce"),
            "category": val("category") or "",
            "estate_type": val("estateType") or "",
            "property_type_uri": val("propertyType") or "",
        })
    df = pd.DataFrame(rows)
    if not len(df):
        return df

    def ptype(uri):
        if uri in PROPERTY_TYPES:
            return PROPERTY_TYPES[uri]
        s = str(uri).rstrip("/").split("/")[-1]
        return s.replace("-", " ").title()
    df["property_type"] = df["property_type_uri"].map(ptype)
    return df

def postcode_info(postcode):
    r = requests.get(f"{POSTCODES_IO}/postcodes/{quote(postcode)}", timeout=15)
    r.raise_for_status()
    return r.json().get("result")

def nearest_postcodes(postcode, limit=20):
    r = requests.get(
        f"{POSTCODES_IO}/postcodes/{quote(postcode)}/nearest",
        params={"limit": min(limit, 100), "radius": 2000},
        timeout=15
    )
    r.raise_for_status()
    return r.json().get("result", [])

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2-lat1)
    dl = math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.atan2(math.sqrt(a), math.sqrt(1-a))

def add_distance(df, subject_pc_info, pc_lookup):
    if df.empty:
        return df
    slat = subject_pc_info.get("latitude")
    slon = subject_pc_info.get("longitude")
    distances = []
    for pc in df["postcode"]:
        info = pc_lookup.get(pc)
        if info and slat is not None and slon is not None:
            distances.append(haversine_m(slat, slon, info["latitude"], info["longitude"]))
        else:
            distances.append(np.nan)
    df = df.copy()
    df["postcode_distance_m"] = distances
    return df

def num_part(paon):
    m = re.match(r"^\s*(\d+)", str(paon))
    return int(m.group(1)) if m else None

def same_street(a, b):
    a = re.sub(r"[^A-Z0-9]", "", str(a).upper())
    b = re.sub(r"[^A-Z0-9]", "", str(b).upper())
    if not a or not b:
        return False
    return a == b or a in b or b in a

def property_label(row):
    p = str(row["paon"]).strip()
    s = str(row["saon"]).strip()
    st = str(row["street"]).strip()
    return " ".join(x for x in [s, p, st] if x)

def relevance_score(row, subject, subject_ptype=None):
    score = 0.0
    # Same postcode is valuable
    if normalise_postcode(row["postcode"]) == subject["postcode"]:
        score += 25
    # Same street is very valuable
    if subject["street"] and same_street(row["street"], subject["street"]):
        score += 30
    # Immediate house-number proximity
    sn = num_part(subject["paon"])
    rn = num_part(row["paon"])
    if sn is not None and rn is not None and subject["street"] and same_street(row["street"], subject["street"]):
        gap = abs(sn-rn)
        if gap <= 2:
            score += 25
        elif gap <= 6:
            score += 16
        elif gap <= 12:
            score += 8
    # Recency
    if pd.notna(row["date"]):
        years = max(0, (pd.Timestamp.today() - row["date"]).days / 365.25)
        score += max(0, 15 - years * 1.5)
    # Type match
    if subject_ptype and row["property_type"] == subject_ptype:
        score += 10
    # Broader postcode-centroid proximity
    d = row.get("postcode_distance_m", np.nan)
    if pd.notna(d):
        score += max(0, 10 - (d / 200))
    return round(min(100, score), 0)

def comp_reason(row, subject):
    reasons = []
    if subject["street"] and same_street(row["street"], subject["street"]):
        sn, rn = num_part(subject["paon"]), num_part(row["paon"])
        if sn is not None and rn is not None:
            gap = abs(sn-rn)
            if gap <= 2:
                reasons.append("immediate neighbour / opposite-number proximity")
            else:
                reasons.append("same street")
        else:
            reasons.append("same street")
    if normalise_postcode(row["postcode"]) == subject["postcode"]:
        reasons.append("same postcode")
    if pd.notna(row["date"]):
        years = (pd.Timestamp.today() - row["date"]).days / 365.25
        if years <= 2:
            reasons.append("recent sale")
    if not reasons:
        d = row.get("postcode_distance_m", np.nan)
        if pd.notna(d):
            reasons.append(f"nearby postcode (~{d:.0f}m centroid distance)")
    return ", ".join(reasons).capitalize()

def estimate_value(comps, subject_type=None):
    if comps.empty:
        return None
    work = comps.copy()
    if subject_type:
        same = work[work["property_type"] == subject_type]
        if len(same) >= 2:
            work = same
    # Weight more recent and more relevant sales.
    rel = work["relevance"].clip(lower=1)
    age_years = ((pd.Timestamp.today() - work["date"]).dt.days / 365.25).clip(lower=0)
    recency = 1 / (1 + age_years * 0.20)
    w = rel * recency
    if w.sum() == 0:
        return work["price"].median()
    return float(np.average(work["price"], weights=w))

# ---------------- UI ----------------
st.title("Brand Portfolios Deal Finder")
st.write("Enter one property address. The app builds the sold-evidence pack around it.")

address = st.text_input(
    "Property address",
    placeholder="e.g. 24 Example Road, Southgate, London, N14 6AA",
    help="For this first live version, include the postcode in the address."
)

col_a, col_b = st.columns([1, 1])
with col_a:
    search_radius = st.select_slider(
        "Comparable search",
        options=["Same postcode only", "Very local", "Wider local"],
        value="Very local"
    )
with col_b:
    years_back = st.selectbox("Sold evidence from", [3, 5, 7, 10, 15, 30], index=3, format_func=lambda x: f"Last {x} years")

run = st.button("Analyse property", type="primary", use_container_width=True)

if run and address:
    subject = parse_subject_address(address)
    if not subject["postcode"]:
        st.error("I need the postcode included in the address for this version, e.g. '24 Example Road, N14 6AA'.")
        st.stop()

    with st.spinner("Building the evidence pack…"):
        try:
            pc_info = postcode_info(subject["postcode"])
            if not pc_info:
                st.error("That postcode could not be found.")
                st.stop()

            if search_radius == "Same postcode only":
                near = [{"postcode": subject["postcode"], "latitude": pc_info["latitude"], "longitude": pc_info["longitude"]}]
            else:
                count = 12 if search_radius == "Very local" else 24
                near = nearest_postcodes(subject["postcode"], count)

            pcs = []
            pc_lookup = {}
            for x in near:
                pc = normalise_postcode(x.get("postcode"))
                if pc and pc not in pcs:
                    pcs.append(pc)
                    pc_lookup[pc] = x
            if subject["postcode"] not in pcs:
                pcs.insert(0, subject["postcode"])
                pc_lookup[subject["postcode"]] = pc_info

            sales = hmlr_query_for_postcodes(pcs)
            if sales.empty:
                st.warning("No Land Registry sales were returned for this local postcode set.")
                st.stop()

            sales = add_distance(sales, pc_info, pc_lookup)
            cutoff = pd.Timestamp.today() - pd.DateOffset(years=years_back)
            sales = sales[sales["date"] >= cutoff].copy()

            # Subject sale history by PAON + street where possible
            subject_sales = sales[
                (sales["paon"].str.upper().str.strip() == subject["paon"].upper().strip())
            ].copy()
            if subject["street"]:
                subject_sales = subject_sales[
                    subject_sales["street"].apply(lambda x: same_street(x, subject["street"]))
                ]

            # Infer subject property type from its latest historic sale, if available
            subject_type = None
            if len(subject_sales):
                subject_type = subject_sales.sort_values("date", ascending=False).iloc[0]["property_type"]

            comps = sales.copy()
            if len(subject_sales):
                # Exclude exact subject address from comparable set
                comp_mask = ~(
                    (comps["paon"].str.upper().str.strip() == subject["paon"].upper().strip()) &
                    comps["street"].apply(lambda x: same_street(x, subject["street"]) if subject["street"] else True)
                )
                comps = comps[comp_mask]

            # Prefer standard/full-market transactions where the category wording permits it.
            comps["relevance"] = comps.apply(lambda r: relevance_score(r, subject, subject_type), axis=1)
            comps["reason"] = comps.apply(lambda r: comp_reason(r, subject), axis=1)
            comps["address"] = comps.apply(property_label, axis=1)
            comps = comps.sort_values(["relevance", "date"], ascending=[False, False])

            # Deduplicate repeated transaction rows if any
            comps = comps.drop_duplicates(subset=["address", "postcode", "price", "date"])
            top = comps.head(12).copy()
            auto_best = top.head(6).copy()
            indicative = estimate_value(auto_best, subject_type)

        except requests.Timeout:
            st.error("The public data service took too long to answer. Try again in a moment.")
            st.stop()
        except requests.RequestException as e:
            st.error(f"The public property-data service could not be reached: {e}")
            st.stop()
        except Exception as e:
            st.error(f"I hit an unexpected data issue: {e}")
            st.stop()

    st.success("Evidence pack created.")

    # Header
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Subject postcode", subject["postcode"])
    h2.metric("Property type", subject_type or "Not yet confirmed")
    h3.metric("Local sales found", len(sales))
    h4.metric("Indicative comp-based figure", money(indicative) if indicative else "—")

    st.caption("The indicative figure is a starting point from nearby sold evidence, not a formal valuation. Condition, size, plot, extensions and exact micro-location still need adjustment.")

    tab1, tab2, tab3, tab4 = st.tabs(["Best Comparables", "Subject Sale History", "Valuation Workbench", "Data Notes"])

    with tab1:
        st.subheader("Best comparable evidence")
        st.write("The app ranks same-street, immediate-number and recent evidence most highly.")

        show = top[["address","postcode","date","price","property_type","postcode_distance_m","relevance","reason"]].copy()
        show["date"] = show["date"].dt.strftime("%d %b %Y")
        show["price"] = show["price"].map(money)
        show["postcode_distance_m"] = show["postcode_distance_m"].map(lambda x: "—" if pd.isna(x) else f"~{x:.0f}m")
        show.columns = ["Comparable","Postcode","Sold","Price","Type","Approx. postcode distance","Relevance /100","Why selected"]
        st.dataframe(show, use_container_width=True, hide_index=True)

        st.info("Distance in this free-data version is between postcode centroids, not front doors. Same-street and house-number proximity are separately weighted, which is why next-door/opposite evidence can still rank at the top.")

    with tab2:
        st.subheader("Previous registered sales of the subject")
        if subject_sales.empty:
            st.write("No matching registered sale was found in the searched period. This can happen if the property has not sold recently, the historic address formatting differs, or a transaction has not yet been registered.")
        else:
            ss = subject_sales.sort_values("date", ascending=False)[["paon","street","postcode","date","price","property_type"]].copy()
            ss["date"] = ss["date"].dt.strftime("%d %b %Y")
            ss["price"] = ss["price"].map(money)
            ss.columns = ["No./Name","Street","Postcode","Sold","Price","Type"]
            st.dataframe(ss, use_container_width=True, hide_index=True)

    with tab3:
        st.subheader("Valuation workbench")
        st.write("This lets you apply your judgement to the automated evidence.")

        selected_addresses = st.multiselect(
            "Comparables to use",
            options=top["address"].tolist(),
            default=auto_best["address"].tolist(),
            help="Untick anything you don't consider genuinely comparable."
        )
        chosen = top[top["address"].isin(selected_addresses)].copy()
        chosen_est = estimate_value(chosen, subject_type) if len(chosen) else None

        c1, c2 = st.columns(2)
        with c1:
            st.metric("Selected-comparable indication", money(chosen_est) if chosen_est else "—")
            asking = st.number_input("Current asking price (£)", min_value=0, value=0, step=5000)
            target_offer = st.number_input("Potential purchase price (£)", min_value=0, value=0, step=5000)
        with c2:
            refurb = st.number_input("Estimated works (£)", min_value=0, value=0, step=5000)
            adjusted_end = st.number_input(
                "Your adjusted end/current value (£)",
                min_value=0,
                value=int(round(chosen_est / 5000) * 5000) if chosen_est else 0,
                step=5000
            )
            other_costs = st.number_input("Other acquisition / finance costs (£)", min_value=0, value=0, step=2500)

        effective_purchase = target_offer or asking
        total = effective_purchase + refurb + other_costs
        value_gap = adjusted_end - total if adjusted_end and total else 0
        margin = (value_gap / total * 100) if total else 0

        m1, m2, m3 = st.columns(3)
        m1.metric("All-in cost", money(total) if total else "—")
        m2.metric("Potential value gap", money(value_gap) if total else "—")
        m3.metric("Gross margin", f"{margin:.1f}%" if total else "—")

        if total:
            if margin >= 15:
                st.success("Strong enough to investigate further, subject to proper valuation, works and legal due diligence.")
            elif margin >= 7.5:
                st.warning("Potential opportunity, but the margin needs tighter evidence.")
            else:
                st.error("Thin value gap on the assumptions entered.")

    with tab4:
        st.subheader("What is automated in this build")
        st.markdown("""
- **One address field** — postcode, house number/name and street are parsed from the address.
- **HM Land Registry sold prices** — live query against Price Paid linked data.
- **Immediate-neighbour logic** — same street and close house numbers receive a much higher relevance score.
- **Nearby postcode search** — widens the evidence set without requiring a paid API key.
- **Subject sale history** — attempts to match historic transactions for the same property.
- **Comparable ranking** — recency, property type, same street, same postcode and proximity are combined.
- **Human override** — you choose which automated comparables actually belong in the valuation.
        """)

        st.subheader("Next data upgrade")
        st.write("""
To get true front-door distances, exact UPRNs, property-level coordinates and better address matching,
the production version should add an authorised address dataset such as OS Places. EPC data can then
be joined for floor area so the app can calculate £/sq ft automatically.
        """)

    st.divider()
    st.caption("Contains HM Land Registry data © Crown copyright and database right 2021. This data is licensed under the Open Government Licence v3.0.")
else:
    st.markdown("""
### What this version does
**Address in → sold evidence out.**

It searches registered sales around the property, pushes immediate same-street evidence to the top,
shows the subject property's historic sales where a match can be made, and lets you select the comparables
you actually want to use.
    """)


# Brand Portfolios Deal Finder V2

## Easiest way to open it on a Mac

1. Unzip this folder.
2. Double-click **START DEAL FINDER.command**.
3. If your Mac blocks it the first time, right-click it and choose **Open**.
4. The first launch installs the small pieces it needs automatically.
5. Your browser opens the Deal Finder.

You only type one thing into the app: the full property address, including postcode.

Example:
`24 Example Road, Southgate, London, N14 6AA`

## What V2 does

- Queries HM Land Registry's live Price Paid linked-data endpoint.
- Pulls registered sold prices around the subject postcode.
- Uses nearby postcodes to create a wider local evidence set.
- Gives same-street and close house-number evidence extra weight.
- Attempts to show the subject property's own previous sale history.
- Ranks the best comparable evidence.
- Lets Jared manually remove weak comparables.
- Creates a simple comp-based valuation indication.
- Provides a valuation/deal workbench.

## Current limitation

The free build uses postcode centroids for approximate wider distance.
It does **not** yet know the precise front-door coordinate for each sale.
Adding OS Places/UPRN and EPC integrations is the next production step.

## Data attribution

Contains HM Land Registry data © Crown copyright and database right 2021.
This data is licensed under the Open Government Licence v3.0.

streamlit>=1.38
pandas>=2.0
numpy>=1.26
requests>=2.31

#!/bin/bash
cd "$(dirname "$0")"

clear
echo "=========================================="
echo "   BRAND PORTFOLIOS DEAL FINDER"
echo "=========================================="
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python is not installed on this Mac."
  echo ""
  echo "Open this page and install the latest Python 3:"
  echo "https://www.python.org/downloads/macos/"
  echo ""
  read -p "Press Enter to close..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "First-time setup. Installing the Deal Finder..."
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
else
  source .venv/bin/activate
fi

echo ""
echo "Opening Brand Portfolios Deal Finder..."
echo "Keep this small window open while you use the app."
echo ""

python -m streamlit run app.py
