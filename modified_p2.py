import streamlit as st
import csv
from datetime import datetime
import os
import re
import json
import ast
import time

st.set_page_config(
    page_title="Part 2 Survey",
    layout="wide",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "stage4_reorganized_top4_thr0_65_with_id.csv")
ASSIGNMENTS_PATH = os.path.join(BASE_DIR, "assignments.csv")
LOCAL_RESULTS_PATH = os.path.join("preview_results_part2.csv")
VALID_CONDITIONS = ["C1", "C2", "C3", "C4", "C5"]
SUPABASE_TABLE = "part2_results"


def get_secret_value(*keys):
    for key in keys:
        try:
            value = st.secrets[key]
            if value:
                return str(value)
        except Exception:
            pass
    return ""


@st.cache_resource
def get_supabase_client():
    try:
        from supabase import create_client
    except ImportError:
        st.error("The `supabase` package is not installed. Run: pip install supabase")
        st.stop()

    url = get_secret_value("SUPABASE_URL", "supabase_url")
    key = get_secret_value("SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "supabase_key")

    try:
        if not url:
            url = str(st.secrets["supabase"]["url"])
        if not key:
            key = str(st.secrets["supabase"]["key"])
    except Exception:
        pass

    if not url or not key:
        st.error(
            "Missing Supabase secrets. Add SUPABASE_URL and SUPABASE_KEY, "
            "or [supabase].url and [supabase].key, to Streamlit secrets."
        )
        st.stop()

    return create_client(url, key)


@st.cache_data
def load_assignments(path):
    assignments = []
    if not os.path.exists(path):
        return assignments

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            assignments.append(row)
    return assignments


def find_assignment(user_id, part):
    target_user_id = str(user_id).strip()
    target_part = str(part).strip().lower()

    for row in load_assignments(ASSIGNMENTS_PATH):
        row_user_id = str(row.get("user_id", "")).strip()
        row_part = str(row.get("part", "")).strip().lower()

        if row_user_id == target_user_id and row_part == target_part:
            condition = str(row.get("condition", "")).strip().upper()
            if condition not in VALID_CONDITIONS:
                return None, "Invalid or missing condition in assignments.csv."

            try:
                start_row = int(row.get("start_row", 0))
                end_row = int(row.get("end_row", 0))
            except Exception:
                return None, "Invalid start_row or end_row in assignments.csv."

            return {
                "user_id": row_user_id,
                "part": row_part,
                "condition": condition,
                "batch_id": str(row.get("batch_id", "")).strip(),
                "start_row": start_row,
                "end_row": end_row,
            }, ""

    return None, "This User ID is not assigned to Part 2."


def split_numbered_list(text):
    pattern = r"(\d+\.)\s+"
    marked = re.sub(pattern, r"|||\1 ", text)
    parts = [l.strip() for l in marked.split("|||") if l.strip()]
    numbered = [p for p in parts if re.match(r"^\d+\. ", p)]
    if len(numbered) < 2:
        return text
    intro = [
        p
        for p in parts
        if not re.match(r"^\d+\. ", p) and parts.index(p) < parts.index(numbered[0])
    ]
    result = "\n".join(numbered)
    if intro:
        result = " ".join(intro) + "\n\n" + result
    return result


def render_candidate_text(text: str):
    if not text:
        st.write("")
        return

    def normalize_markdown_headings(raw: str) -> str:
        raw = re.sub(r"(?<!\n)\s+(#{1,6})\s+", r"\n\1 ", raw)
        raw = split_numbered_list(raw)

        def _shift_heading(match):
            hashes = match.group(1)
            level = min(len(hashes) + 3, 6)
            return "#" * level + " "

        return re.sub(r"(?m)^(#{1,6})\s+", _shift_heading, raw)

    pattern = re.compile(r"\[(?i:sponsor(?:ed)?)\s+(.*?)\]", flags=re.DOTALL)
    cursor = 0
    for match in pattern.finditer(text):
        normal_part = text[cursor:match.start()].strip()
        if normal_part:
            st.markdown(normalize_markdown_headings(normal_part))
        sponsored_content = match.group(1).strip()
        if sponsored_content:
            st.markdown(
                f"<p style='color:#8A8A8A;'><strong>Sponsored:</strong> {sponsored_content}</p>",
                unsafe_allow_html=True,
            )
        cursor = match.end()
    tail = text[cursor:].strip()
    if tail:
        st.markdown(normalize_markdown_headings(tail))


def first_nonempty(row, names):
    for name in names:
        value = row.get(name, "")
        if value is not None and str(value).strip():
            return value
    return ""


def extract_sponsored_candidates(*texts):
    candidates = []
    pattern = re.compile(r"\[(?i:sponsor(?:ed)?)\s+(.*?)\]", flags=re.DOTALL)
    for text in texts:
        if not text:
            continue
        for match in pattern.finditer(str(text)):
            item = re.sub(r"\s+", " ", match.group(1)).strip()
            if item and item not in candidates:
                candidates.append(item)
    return candidates


@st.cache_data
def load_data(path):
    samples = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            original_response = first_nonempty(row, ["original_response", "Original_response"])

            # New logic: C2/C4 use best_candidate_text.
            # If best_candidate_text is missing/empty, recover it from best_position.
            # If best_position is missing/empty, fall back to candidate4.
            best_position = str(row.get("best_position", "")).strip()
            if best_position not in ["candidate1", "candidate2", "candidate3", "candidate4"]:
                best_position = "candidate4"

            candidate_4 = first_nonempty(row, ["candidate4", "candidate_4", "Candidate4", "Candidate_4"])
            best_candidate_text = first_nonempty(row, ["best_candidate_text"])
            if not best_candidate_text:
                best_candidate_text = first_nonempty(row, [best_position])
            if not best_candidate_text:
                best_candidate_text = candidate_4

            # New logic: C3 uses the prime column corresponding to best_position.
            prime_col = f"{best_position}'"
            best_candidate_prime_text = first_nonempty(row, [prime_col])
            if not best_candidate_prime_text:
                best_candidate_prime_text = first_nonempty(row, ["candidate4'"])

            # New logic: ad options come from ad_entities, not entities_json.
            entities_raw = first_nonempty(row, ["ad_entities"])
            if not entities_raw:
                sponsored_candidates = extract_sponsored_candidates(best_candidate_prime_text, best_candidate_text)
                if sponsored_candidates:
                    entities_raw = json.dumps(sponsored_candidates, ensure_ascii=False)

            if original_response and (best_candidate_text or best_candidate_prime_text):
                samples.append({
                    "id": row.get("id") or row.get("conversation_id", ""),
                    "turn": row.get("turn", ""),
                    "context": row.get("context", ""),
                    "question": row.get("question", ""),
                    "original_response": original_response,
                    "best_position": best_position,
                    "best_candidate_text": best_candidate_text,
                    "best_candidate_prime_text": best_candidate_prime_text,
                    "ad_entities": entities_raw,
                })
    return samples


# ================== Condition helpers ==================
CONDITION_CONFIG = {
    "C1": {"ad_present": 0, "disclosure_type": "none", "show_global_notice": False, "show_local_label": False},
    "C2": {"ad_present": 1, "disclosure_type": "none", "show_global_notice": False, "show_local_label": False},
    "C3": {"ad_present": 1, "disclosure_type": "local", "show_global_notice": False, "show_local_label": True},
    "C4": {"ad_present": 1, "disclosure_type": "global", "show_global_notice": True, "show_local_label": False},
    "C5": {"ad_present": 0, "disclosure_type": "global", "show_global_notice": True, "show_local_label": False},
}


CONDITION_DESC = {
    "C1": "No ad, no disclosure",
    "C2": "Ad present, no disclosure",
    "C3": "Ad present, local Sponsored label",
    "C4": "Ad present, global notice at top",
    "C5": "No ad, global notice at top",
}


def init_session():
    if "page" not in st.session_state:
        st.session_state.page = "user_id"
    if "user_id" not in st.session_state:
        st.session_state.user_id = ""
    if "assignment" not in st.session_state:
        st.session_state.assignment = None
    if "active_condition" not in st.session_state:
        st.session_state.active_condition = None
    if "sample_history" not in st.session_state:
        st.session_state.sample_history = []
    if "override_sample_id" not in st.session_state:
        st.session_state.override_sample_id = None


def show_user_id_page():
    st.title("Part 2 Survey")
    st.markdown("Please enter your User ID to begin.")

    user_id = st.text_input(
        "User ID",
        value=st.session_state.get("user_id", ""),
        placeholder="e.g. user_001",
    )

    continue_clicked = st.button("Continue", use_container_width=True)

    if continue_clicked:
        if not user_id.strip():
            st.warning("Please enter your User ID.")
            st.stop()

        assignment, error_msg = find_assignment(user_id.strip(), "part2")
        if assignment is None:
            st.warning(error_msg)
            st.stop()

        st.session_state.user_id = user_id.strip()
        st.session_state.assignment = assignment
        st.session_state.active_condition = assignment["condition"]
        st.session_state.sample_history = []
        st.session_state.override_sample_id = None
        st.session_state.page = "calibration"
        st.rerun()


def show_calibration_page(condition):
    st.title("Response Rating Guide")
    st.markdown(f"Condition: **{condition}**")

    st.markdown(
        """
You will read a user question and an AI assistant response. Please evaluate the response as a normal user would. Focus only on the response shown on the current page; do not compare it with previous responses.

**Trustworthiness**  
Rate whether the information in the response seems reliable, balanced, and not misleading. A trustworthy response does not make unsupported claims or hide important limitations.

**Naturalness**  
Rate whether the response reads smoothly and fits the user's question. A natural response should not feel awkward, abrupt, repetitive, or out of place.

**Influence**  
Rate whether the response seems to push you toward a product, service, action, opinion, or decision. A high score means the response feels persuasive or promotional; a low score means it feels neutral or informational.

**Ad detection**  
For the question “Do you think it contains ad?”, choose **Yes** only if some part of the response feels like a sponsored recommendation, promotion, or advertisement. Choose **No** if you do not think the response contains one.

**Ad identification**  
If you choose **Yes**, select the option that best matches the ad or recommendation you noticed.

**Helpfulness**  
Rate whether the response answers the user's question and provides useful, relevant information.

**Overall quality**  
Give your overall judgment of the response, considering trustworthiness, naturalness, helpfulness, and whether the response feels appropriate.
        """
    )

    col_start, col_back = st.columns([1, 1])
    with col_start:
        start_clicked = st.button("Start Survey", use_container_width=True)
    with col_back:
        back_clicked = st.button("Back", use_container_width=True)

    if back_clicked:
        st.session_state.page = "user_id"
        st.rerun()

    if start_clicked:
        st.session_state.page = "survey"
        st.rerun()


def get_sample_for_condition(sample, condition):
    if condition in ["C1", "C5"]:
        return sample["original_response"], "original_response"
    if condition == "C3":
        response_source = f"{sample.get('best_position', 'candidate4')}'"
        return sample.get("best_candidate_prime_text", ""), response_source
    if condition in ["C2", "C4"]:
        return sample.get("best_candidate_text", ""), "best_candidate_text"
    return "", ""


# ================== Task helpers ==================
def build_task_pool_for_condition(samples, condition):
    task_pool = []
    for sample in samples:
        response_text, response_source = get_sample_for_condition(sample, condition)
        if response_text and str(response_text).strip():
            task_pool.append({
                "sample_id": str(sample.get("id", "")),
                "sample": sample,
                "response_text": response_text,
                "response_source": response_source,
            })
    return task_pool


def is_skipped_value(value) -> bool:
    return str(value).strip().lower() in {"yes", "true", "1", "t"}


def load_completed_sample_ids(user_id, condition, include_skipped: bool = False):
    supabase = get_supabase_client()

    try:
        response = (
            supabase.table(SUPABASE_TABLE)
            .select("sample_id, skipped")
            .eq("user_id", user_id)
            .eq("condition", condition)
            .execute()
        )
    except Exception as e:
        st.error(f"Failed to load completed samples from Supabase: {e}")
        st.stop()

    completed = set()
    for row in response.data or []:
        if not include_skipped and is_skipped_value(row.get("skipped", "")):
            continue
        sample_id = row.get("sample_id", "")
        if sample_id:
            completed.add(str(sample_id))

    return completed


def get_next_unfinished_task(task_pool, completed_ids):
    for task in task_pool:
        if task["sample_id"] not in completed_ids:
            return task
    return None


def get_task_by_sample_id(task_pool, sample_id):
    for task in task_pool:
        if task["sample_id"] == sample_id:
            return task
    return None


def get_task_position(task_pool, current_task):
    current_id = current_task["sample_id"]
    for idx, task in enumerate(task_pool, start=1):
        if task["sample_id"] == current_id:
            return idx
    return 1


# ================== Main Streamlit App ==================
def scale_options():
    return [str(i) for i in range(1, 6)]


def parse_entities(entities_raw: str):
    def clean_item(item):
        if item is None:
            return ""
        if isinstance(item, dict):
            for key in ["name", "entity", "title", "brand", "product", "text", "value"]:
                value = item.get(key)
                if value is not None and str(value).strip():
                    return re.sub(r"\s+", " ", str(value)).strip()
            return re.sub(r"\s+", " ", json.dumps(item, ensure_ascii=False)).strip()
        return re.sub(r"\s+", " ", str(item)).strip()

    def normalize(parsed):
        if isinstance(parsed, str):
            stripped = parsed.strip()
            if not stripped:
                return []
            for parser in (json.loads, ast.literal_eval):
                try:
                    return normalize(parser(stripped))
                except Exception:
                    pass
            if ";" in stripped:
                return [clean_item(x) for x in stripped.split(";") if clean_item(x)]
            if "," in stripped and not stripped.startswith("http"):
                return [clean_item(x) for x in stripped.split(",") if clean_item(x)]
            return [clean_item(stripped)]
        if isinstance(parsed, list):
            return [clean_item(x) for x in parsed if clean_item(x)]
        if isinstance(parsed, dict):
            for key in ["entities", "entity", "ads", "ad_entities", "items", "matches"]:
                if key in parsed:
                    return normalize(parsed[key])
            return [clean_item(parsed)]
        return []

    seen = set()
    result = []
    for item in normalize(entities_raw):
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def parse_context(context_raw: str):
    if not context_raw or not str(context_raw).strip():
        return []

    text = str(context_raw)
    pattern = re.compile(
        r"\[User:(.*?)\]\s*\[Response:(.*?)\]",
        flags=re.DOTALL,
    )

    exchanges = []
    for match in pattern.finditer(text):
        user_text = match.group(1).strip()
        assistant_text = match.group(2).strip()

        if user_text or assistant_text:
            exchanges.append({
                "user": user_text,
                "assistant": assistant_text,
            })

    return exchanges


def render_previous_context(context_raw: str, turn):
    try:
        turn_num = int(turn)
    except Exception:
        turn_num = 1

    if turn_num <= 1:
        return

    exchanges = parse_context(context_raw)
    if not exchanges:
        return

    with st.expander("Previous conversation context", expanded=True):
        for idx, exchange in enumerate(exchanges):
            if exchange.get("user"):
                st.markdown("**User:**")
                st.markdown(exchange["user"])

            if exchange.get("assistant"):
                st.markdown("**Assistant:**")
                render_candidate_text(exchange["assistant"])

            if idx < len(exchanges) - 1:
                st.divider()


def render_question_title(label: str):
    st.markdown(
        f"<p style='font-size:18px; font-weight:600; margin: 0.5rem 0 0.2rem 0;'>{label}</p>",
        unsafe_allow_html=True,
    )


def set_scale_value(session_key: str, value: str):
    st.session_state[session_key] = value


def safe_key_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "sample"))[:80]


def inject_scale_css_once():
    st.html(
        """
        <style>
        .scale-endpoint {
            font-size: 18px;
            color: #4F4F4F;
            line-height: 1.25;
            padding-top: 1.15rem;
        }

        .scale-right {
            text-align: right;
        }

        div[class*="st-key-scale_btn_"] button {
            border-radius: 999px !important;
            padding: 0 !important;
            min-width: unset !important;
            min-height: unset !important;
            box-shadow: none !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
        }

        div[class*="st-key-scale_btn_"] {
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            height: 76px !important;
        }

        div[class*="st-key-scale_btn_"] button p {
            font-size: 0 !important;
            line-height: 0 !important;
            margin: 0 !important;
            color: transparent !important;
        }

        div[class*="st-key-scale_btn_"] button:hover,
        div[class*="st-key-scale_btn_"] button:focus,
        div[class*="st-key-scale_btn_"] button:active {
            box-shadow: none !important;
            outline: none !important;
        }
        </style>
        """
    )


def inject_layout_css():
    st.html(
        """
        <style>
        .block-container {
            max-width: 1100px;
            padding-top: 3rem;
            padding-left: 2rem;
            padding-right: 2rem;
        }

        div[role="radiogroup"] label p {
            font-size: 18px !important;
        }
        </style>
        """
    )


def render_scale_question(q_key: str, label: str, left_text: str, right_text: str, condition: str, sample_id: str):
    inject_scale_css_once()
    render_question_title(label)

    sample_key = safe_key_part(sample_id)
    session_key = f"scale_value_{condition}_{q_key}_{sample_key}"

    if session_key not in st.session_state:
        st.session_state[session_key] = None

    selected_value = st.session_state.get(session_key)

    sizes = [50, 40, 30, 40, 50]

    colors = ["#35A978", "#35A978", "#9EA3AA", "#87589A", "#87589A"]

    dynamic_css = ["<style>"]

    for idx, (size, color) in enumerate(zip(sizes, colors), start=1):
        key = f"scale_btn_{condition}_{q_key}_{sample_key}_{idx}"
        is_selected = selected_value == str(idx)

        background = color if is_selected else "#FFFFFF"
        border_width = 0 if is_selected else 4

        dynamic_css.append(
            f"""
            div[class*="st-key-{key}"] button {{
                width: {size}px !important;
                height: {size}px !important;
                border: {border_width}px solid {color} !important;
                background-color: {background} !important;
            }}

            div[class*="st-key-{key}"] button:hover,
            div[class*="st-key-{key}"] button:focus,
            div[class*="st-key-{key}"] button:active {{
                width: {size}px !important;
                height: {size}px !important;
                border: {border_width}px solid {color} !important;
                background-color: {background} !important;
            }}
            """
        )

    dynamic_css.append("</style>")
    st.html("\n".join(dynamic_css))

    cols = st.columns([2.4, 0.9, 0.75, 0.6, 0.75, 0.9, 2.4])

    with cols[0]:
        st.markdown(
            f"<div class='scale-endpoint'>{left_text}</div>",
            unsafe_allow_html=True,
        )

    for idx in range(1, 6):
        with cols[idx]:
            st.button(
                "\u200b",
                key=f"scale_btn_{condition}_{q_key}_{sample_key}_{idx}",
                on_click=set_scale_value,
                args=(session_key, str(idx)),
            )

    with cols[6]:
        st.markdown(
            f"<div class='scale-endpoint scale-right'>{right_text}</div>",
            unsafe_allow_html=True,
        )

    return st.session_state.get(session_key)


def render_radio_question(q_key: str, label: str, options: list, condition: str, sample_id: str, horizontal: bool = True):
    render_question_title(label)
    options = [str(x) for x in options if str(x).strip()]
    if not options:
        st.info("No ad options were found for this response.")
        return ""
    return st.radio(
        label,
        options,
        index=None,
        key=f"{condition}_{q_key}_{sample_id}",
        label_visibility="collapsed",
        horizontal=horizontal,
    )


def save_result(sample, user_id, batch_id, condition, response_source, choices, reading_time_seconds, skipped: bool = False):
    payload = {
        "user_id": user_id,
        "batch_id": batch_id,
        "condition": condition,
        "sample_id": sample["id"],
        "Q1": choices.get("Q1", "") if not skipped else "",
        "Q2": choices.get("Q2", "") if not skipped else "",
        "Q3": choices.get("Q3", "") if not skipped else "",
        "Q4": choices.get("Q4", "") if not skipped else "",
        "Q5": choices.get("Q5", "") if not skipped else "",
        "Q6": choices.get("Q6", "") if not skipped else "",
        "Q7": choices.get("Q7", "") if not skipped else "",
        "reading_time_seconds": reading_time_seconds,
        "skipped": skipped,
    }

    supabase = get_supabase_client()

    try:
        (
            supabase.table(SUPABASE_TABLE)
            .upsert(payload, on_conflict="user_id,condition,sample_id")
            .execute()
        )
    except Exception as e:
        st.error(
            "Failed to save result to Supabase. "
            "Make sure part2_results has a unique constraint on (user_id, condition, sample_id). "
            f"Error: {e}"
        )
        st.stop()


def go_back_to_previous_sample(condition):
    history = st.session_state.get("sample_history", [])
    if not history:
        return

    previous_sample_id = history.pop()
    st.session_state.sample_history = history
    st.session_state.override_sample_id = previous_sample_id

    timer_key = f"reading_start_{condition}_{previous_sample_id}"
    st.session_state[timer_key] = time.time()

    st.rerun()


inject_layout_css()
init_session()

if st.session_state.page == "user_id":
    show_user_id_page()
    st.stop()

assignment = st.session_state.get("assignment")
if assignment is None:
    st.session_state.page = "user_id"
    st.rerun()

condition = assignment["condition"]
user_id = st.session_state.user_id
batch_id = assignment.get("batch_id", "")

if st.session_state.page == "calibration":
    show_calibration_page(condition)
    st.stop()

data = load_data(DATA_PATH)
if not data:
    st.error(f"No data found. Please check: {DATA_PATH}")
    st.stop()

start_row = assignment["start_row"]
end_row = assignment["end_row"]
data = data[start_row:end_row]

config = CONDITION_CONFIG[condition]

task_pool = build_task_pool_for_condition(data, condition)
completed_ids = load_completed_sample_ids(user_id, condition, include_skipped=False)
handled_ids = load_completed_sample_ids(user_id, condition, include_skipped=True)

override_sample_id = st.session_state.get("override_sample_id")
if override_sample_id:
    current_task = get_task_by_sample_id(task_pool, override_sample_id)
    if current_task is None:
        st.session_state.override_sample_id = None
        current_task = get_next_unfinished_task(task_pool, handled_ids)
else:
    current_task = get_next_unfinished_task(task_pool, handled_ids)

if not task_pool:
    st.error("No valid samples were found for this condition.")
    st.stop()

if current_task is None:
    st.success("All samples for this condition have been completed.")
    st.stop()

sample = current_task["sample"]
response_text = current_task["response_text"]
response_source = current_task["response_source"]

timer_key = f"reading_start_{condition}_{current_task['sample_id']}"
if timer_key not in st.session_state:
    st.session_state[timer_key] = time.time()


col_left, col_right = st.columns([3, 1])
with col_left:
    st.header(condition)
with col_right:
    if st.button(
        "Back to previous sample",
        key="back_previous_sample",
        disabled=not bool(st.session_state.get("sample_history", [])),
        use_container_width=True,
    ):
        go_back_to_previous_sample(condition)

st.markdown(
    f"<div style='color:#666; font-size:0.95rem;'>Completed tasks: {len(completed_ids)} / {len(task_pool)}</div>",
    unsafe_allow_html=True,
)

current_position = get_task_position(task_pool, current_task)
st.markdown(
    f"<div style='color:#666; font-size:0.95rem;'>Current sample: {current_position} / {len(task_pool)}</div>",
    unsafe_allow_html=True,
)

render_previous_context(sample.get("context", ""), sample.get("turn", 1))

st.markdown(f"### Current Question\n{sample['question']}")
st.subheader("Current Response")

if config["show_global_notice"]:
    st.markdown(
        """
        <div style="
            background: #F7E9B6;
            border: 1px solid #E2C76E;
            color: #4B3B0A;
            padding: 0.75rem 1rem;
            border-radius: 8px;
            font-weight: 600;
            margin-bottom: 0.75rem;
        ">
            Notice: This response may contain sponsored content.
        </div>
        """,
        unsafe_allow_html=True,
    )

render_candidate_text(response_text)

choices = {}


scale_questions_before_ad = [
    ("Q1", "How much would you trust the information in this response?", "Not at all trustworthy", "Very trustworthy"),
    ("Q2", "How naturally does this response read?", "Very unnatural", "Very natural"),
]

for q_key, label, left_text, right_text in scale_questions_before_ad:
    choices[q_key] = render_scale_question(
        q_key,
        label,
        left_text,
        right_text,
        condition,
        sample["id"],
    )

ad_question = "Do you think it contains ad?"
choices["Q4"] = render_radio_question(
    "q4",
    ad_question,
    ["Yes", "No"],
    condition,
    sample["id"],
    horizontal=True,
)

entities_list = parse_entities(sample.get("ad_entities", ""))
if choices.get("Q4") == "Yes":
    ad_entity_question = "Which ad do you think it contains?"
    choices["Q5"] = render_radio_question(
        "q5",
        ad_entity_question,
        entities_list,
        condition,
        sample["id"],
        horizontal=False,
    )
else:
    choices["Q5"] = ""

scale_questions_after_ad = [
    ("Q3", "To what extent does this response feel like it's trying to influence your decisions or behavior?", "Not at all", "Very much"),
    ("Q6", "How helpful is this response in answering the question?", "Not at all helpful", "Extremely helpful"),
    ("Q7", "Overall, how would you rate the quality of this response?", "Very poor", "Excellent"),
]

for q_key, label, left_text, right_text in scale_questions_after_ad:
    choices[q_key] = render_scale_question(
        q_key,
        label,
        left_text,
        right_text,
        condition,
        sample["id"],
    )


col_submit, col_spacer, col_skip = st.columns([1, 0.5, 1])
with col_submit:
    submit_clicked = st.button("Submit", use_container_width=True)

with col_skip:
    skip_clicked = st.button("Skip", use_container_width=True)

if submit_clicked:
    required_keys = ["Q1", "Q2", "Q3", "Q4", "Q6", "Q7"]
    if choices.get("Q4") == "Yes":
        required_keys.append("Q5")

    missing_required = [k for k in required_keys if not choices.get(k)]
    if missing_required:
        st.warning("Please answer all required questions before submitting.")
        st.stop()

    reading_time_seconds = round(time.time() - st.session_state[timer_key], 3)

    save_result(
        sample,
        user_id,
        batch_id,
        condition,
        response_source,
        choices,
        reading_time_seconds,
    )

    if not st.session_state.get("override_sample_id"):
        history = st.session_state.get("sample_history", [])
        if not history or history[-1] != current_task["sample_id"]:
            history.append(current_task["sample_id"])
        st.session_state.sample_history = history

    st.session_state.override_sample_id = None

    if timer_key in st.session_state:
        del st.session_state[timer_key]

    st.success("Saved!")
    st.rerun()

if skip_clicked:
    reading_time_seconds = round(time.time() - st.session_state[timer_key], 3)

    save_result(
        sample,
        user_id,
        batch_id,
        condition,
        response_source,
        choices,
        reading_time_seconds,
        skipped=True,
    )

    if not st.session_state.get("override_sample_id"):
        history = st.session_state.get("sample_history", [])
        if not history or history[-1] != current_task["sample_id"]:
            history.append(current_task["sample_id"])
        st.session_state.sample_history = history

    st.session_state.override_sample_id = None

    if timer_key in st.session_state:
        del st.session_state[timer_key]

    st.info("Skipped.")
    st.rerun()