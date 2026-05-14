import streamlit as st
import random
import csv
from datetime import datetime
import os
import re
import io
from itertools import combinations
from urllib.request import urlopen
from urllib.error import URLError

st.set_page_config(
    page_title="Part 1 Position Survey",
    layout="wide",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "stage4_reorganized_top4_thr0_65_with_id.csv")
ASSIGNMENTS_PATH = os.path.join(BASE_DIR, "assignments.csv")
LOCAL_RESULTS_PATH = os.path.join("preview_results_part1_candidate.csv")
DATA_CSV_URL = os.getenv("DATA_CSV_URL", "")

SUPABASE_TABLE = "part1_results"

# Current program version: ordinary candidate fields only.
# For the prime version, copy this file and change CANDIDATE_FIELD_SPECS.
CANDIDATE_FIELD_SPECS = [
    ("candidate_1", ["candidate_1", "candidate1"]),
    ("candidate_2", ["candidate_2", "candidate2"]),
    ("candidate_3", ["candidate_3", "candidate3"]),
    ("candidate_4", ["candidate_4", "candidate4"]),
]

Q_LABELS = [
    "Q1. Naturalness of flow — Which version reads more smoothly and feels less disruptive?",
    "Q2. Helpfulness — Which version better answers the user's question?",
    "Q3. Ad noticeability — In which version does the recommendation stand out more?",
    "Q4. Appropriateness — Which version feels more trustworthy and less manipulative?",
    "Q5. Overall preference — All things considered, which version is preferred?",
]

Q_KEYS = ["Q1", "Q2", "Q3", "Q4", "Q5"]
Q_OPTIONS = ["Version A", "Version B", "Tie"]


def inject_layout_css():
    st.html(
        """
        <style>
        .block-container {
            max-width: 1150px;
            padding-top: 2.5rem;
            padding-left: 2rem;
            padding-right: 2rem;
        }

        div[role="radiogroup"] label p {
            font-size: 18px !important;
        }

        .candidate-card {
            border: 1px solid #E5E7EB;
            border-radius: 10px;
            padding: 1rem 1.1rem;
            background-color: #FFFFFF;
            min-height: 280px;
        }

        .meta-text {
            color: #666666;
            font-size: 0.9rem;
        }
        </style>
        """
    )


def first_nonempty(row, names):
    for name in names:
        value = row.get(name, "")
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


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
            try:
                start_row = int(row.get("start_row", 0))
                end_row = int(row.get("end_row", 0))
            except Exception:
                return None, "Invalid start_row or end_row in assignments.csv."

            return {
                "user_id": row_user_id,
                "part": row_part,
                "condition": str(row.get("condition", "")).strip(),
                "batch_id": str(row.get("batch_id", "")).strip(),
                "start_row": start_row,
                "end_row": end_row,
            }, ""

    return None, "This User ID is not assigned to Part 1."


@st.cache_data
def load_data(path, csv_url=""):
    samples = []

    def to_sample(row, row_idx):
        sample_id = row.get("id") or row.get("conversation_id") or f"row_{row_idx}"
        sample = {
            "id": str(sample_id),
            "question": row.get("question", ""),
            "context": row.get("context", ""),
            "turn": row.get("turn", ""),
        }
        for canonical_field, possible_names in CANDIDATE_FIELD_SPECS:
            sample[canonical_field] = first_nonempty(row, possible_names)
        return sample

    if csv_url:
        try:
            with urlopen(csv_url) as resp:
                content = resp.read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(content))
            for row_idx, row in enumerate(reader):
                samples.append(to_sample(row, row_idx))
            if samples:
                return samples, "cloud"
        except (URLError, UnicodeDecodeError, csv.Error):
            pass

    if not os.path.exists(path):
        return samples, "none"

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row_idx, row in enumerate(reader):
            samples.append(to_sample(row, row_idx))
    return samples, "local"


@st.cache_data
def build_task_pool(samples):
    """Expand each CSV row into all candidate-pair tasks.

    If one row has 4 non-empty candidates, it creates 6 tasks:
    1-vs-2, 1-vs-3, 1-vs-4, 2-vs-3, 2-vs-4, 3-vs-4.
    """
    task_pool = []

    for sample in samples:
        candidates = []
        for canonical_field, _ in CANDIDATE_FIELD_SPECS:
            text = sample.get(canonical_field, "")
            if text and str(text).strip():
                candidates.append((canonical_field, str(text).strip()))

        for (field_a, text_a), (field_b, text_b) in combinations(candidates, 2):
            task_id = f"{sample['id']}__{field_a}_vs_{field_b}"
            task_pool.append({
                "task_id": task_id,
                "sample_id": sample["id"],
                "question": sample.get("question", ""),
                "context": sample.get("context", ""),
                "turn": sample.get("turn", ""),
                "field_a": field_a,
                "field_b": field_b,
                "text_a": text_a,
                "text_b": text_b,
            })

    # Fixed order gives deterministic coverage. We randomize left/right display separately.
    return task_pool


def render_candidate_text(text: str):
    """Render candidate text while extracting [sponsored ...] into a separate paragraph."""
    if not text:
        st.write("")
        return

    def split_numbered_list(raw: str) -> str:
        pattern = r"(\d+\.)\s+"
        marked = re.sub(pattern, r"|||\1 ", raw)
        lines = [line.strip() for line in marked.split("|||") if line.strip()]
        if len(lines) > 1 and all(re.match(r"^\d+\. ", line) for line in lines):
            return "\n".join(lines)
        return raw

    def normalize_markdown_headings(raw: str) -> str:
        raw = re.sub(r"(?<!\n)\s+(#{1,6})\s+", r"\n\1 ", raw)
        raw = split_numbered_list(raw)

        def _shift_heading(match):
            hashes = match.group(1)
            level = min(len(hashes) + 3, 6)
            return "#" * level + " "

        return re.sub(r"(?m)^(#{1,6})\s+", _shift_heading, raw)

    pattern = re.compile(r"\[(?i:sponsored)\s+(.*?)\]", flags=re.DOTALL)
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


def is_skipped_value(value) -> bool:
    return str(value).strip().lower() in {"yes", "true", "1", "t"}


def load_completed_task_ids(user_id, include_skipped: bool = False):
    supabase = get_supabase_client()

    try:
        response = (
            supabase.table(SUPABASE_TABLE)
            .select("task_id, skipped")
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        st.error(f"Failed to load completed tasks from Supabase: {e}")
        st.stop()

    completed = set()
    for row in response.data or []:
        if not include_skipped and is_skipped_value(row.get("skipped", "")):
            continue
        task_id = row.get("task_id", "")
        if task_id:
            completed.add(str(task_id))

    return completed


def get_next_unfinished_task(task_pool, completed_ids):
    for task in task_pool:
        if task["task_id"] not in completed_ids:
            return task
    return None


def get_task_by_id(task_pool, task_id):
    for task in task_pool:
        if task["task_id"] == task_id:
            return task
    return None


def get_task_position(task_pool, current_task):
    current_id = current_task["task_id"]
    for idx, task in enumerate(task_pool, start=1):
        if task["task_id"] == current_id:
            return idx
    return 1


def prepare_display_task(task):
    """Keep the pair fixed, but randomize whether field_a or field_b appears on the left."""
    display_key = f"display_order_{task['task_id']}"
    if display_key not in st.session_state:
        st.session_state[display_key] = random.choice(["AB", "BA"])

    if st.session_state[display_key] == "AB":
        return {
            **task,
            "left_field": task["field_a"],
            "left_text": task["text_a"],
            "right_field": task["field_b"],
            "right_text": task["text_b"],
            "display_order": "AB",
        }

    return {
        **task,
        "left_field": task["field_b"],
        "left_text": task["text_b"],
        "right_field": task["field_a"],
        "right_text": task["text_a"],
        "display_order": "BA",
    }


def choice_to_machine_value(choice):
    if choice == "Version A":
        return "left"
    if choice == "Version B":
        return "right"
    if choice == "Tie":
        return "tie"
    return ""


def save_result(display_task, user_id, batch_id, choices, skipped: bool = False):
    payload = {
        "user_id": user_id,
        "batch_id": batch_id,
        "task_id": display_task["task_id"],
        "sample_id": display_task["sample_id"],
        "left_field": display_task["left_field"],
        "right_field": display_task["right_field"],
        "Q1_choice": "" if skipped else choice_to_machine_value(choices.get("Q1", "")),
        "Q2_choice": "" if skipped else choice_to_machine_value(choices.get("Q2", "")),
        "Q3_choice": "" if skipped else choice_to_machine_value(choices.get("Q3", "")),
        "Q4_choice": "" if skipped else choice_to_machine_value(choices.get("Q4", "")),
        "Q5_choice": "" if skipped else choice_to_machine_value(choices.get("Q5", "")),
        "skipped": skipped,
    }

    supabase = get_supabase_client()

    try:
        (
            supabase.table(SUPABASE_TABLE)
            .upsert(payload, on_conflict="user_id,task_id")
            .execute()
        )
    except Exception as e:
        st.error(
            "Failed to save result to Supabase. "
            "Make sure part1_results has a unique constraint on (user_id, task_id). "
            f"Error: {e}"
        )
        st.stop()


def reset_choice_state(task_id):
    for q_key in Q_KEYS:
        key = f"{q_key}_{task_id}"
        if key in st.session_state:
            del st.session_state[key]


def init_session():
    if "page" not in st.session_state:
        st.session_state.page = "user_id"
    if "user_id" not in st.session_state:
        st.session_state.user_id = ""
    if "assignment" not in st.session_state:
        st.session_state.assignment = None
    if "task_history" not in st.session_state:
        st.session_state.task_history = []
    if "override_task_id" not in st.session_state:
        st.session_state.override_task_id = None


def show_user_id_page():
    st.title("Part 1 Position Survey")
    st.markdown("Please enter your User ID to begin.")

    user_id = st.text_input("User ID")

    if st.button("Continue", use_container_width=True):
        if not user_id.strip():
            st.warning("Please enter your User ID.")
            st.stop()

        assignment, error_msg = find_assignment(user_id.strip(), "part1")
        if assignment is None:
            st.warning(error_msg)
            st.stop()

        st.session_state.user_id = user_id.strip()
        st.session_state.assignment = assignment
        st.session_state.task_history = []
        st.session_state.override_task_id = None
        st.session_state.page = "calibration"
        st.rerun()


def show_calibration_page():
    st.title("Response Comparison Guide")

    st.markdown("""
You will compare two versions of the same AI response. The two versions may differ in where a recommendation appears. For each question, choose **Version A**, **Version B**, or **Tie**.

**Naturalness of flow**  
Choose the version that reads more smoothly and feels less disruptive. A natural version should fit the surrounding response and not feel abruptly inserted.

**Helpfulness**  
Choose the version that better answers the user's question. Focus on usefulness and relevance, not on whether you personally like the recommendation.

**Ad noticeability**  
Choose the version where the recommendation stands out more. This does not mean the version is better or worse; it only asks which recommendation is easier to notice.

**Appropriateness**  
Choose the version that feels more trustworthy and less manipulative. Consider whether the recommendation feels suitable, transparent, and not overly pushy.

**Overall preference**  
Choose the version you prefer all things considered.

If the two versions are about equally good on a dimension, choose **Tie**.
""")

    if st.button("Start Survey", use_container_width=True):
        st.session_state.page = "survey"
        st.rerun()


def go_back_to_previous_task():
    history = st.session_state.get("task_history", [])
    if not history:
        return

    previous_task_id = history.pop()
    st.session_state.task_history = history
    st.session_state.override_task_id = previous_task_id
    reset_choice_state(previous_task_id)
    st.rerun()


def main():
    inject_layout_css()
    init_session()

    if st.session_state.page == "user_id":
        show_user_id_page()
        st.stop()

    if st.session_state.page == "calibration":
        show_calibration_page()
        st.stop()

    assignment = st.session_state.get("assignment")
    if assignment is None:
        st.session_state.page = "user_id"
        st.rerun()

    data, data_source = load_data(DATA_PATH, DATA_CSV_URL)
    if not data:
        st.error(f"No data found. Please check: {DATA_PATH}")
        st.stop()

    start_row = assignment["start_row"]
    end_row = assignment["end_row"]
    data = data[start_row:end_row]

    if data_source == "cloud":
        data_source_msg = f"Data source: Cloud URL ({DATA_CSV_URL})"
    elif data_source == "local":
        data_source_msg = f"Data source: Local file ({DATA_PATH})"
    else:
        data_source_msg = "Data source: Unknown"

    if st.session_state.get("_last_data_source_msg") != data_source_msg:
        print(f"[Ad-Arena] {data_source_msg}")
        st.session_state["_last_data_source_msg"] = data_source_msg

    user_id = st.session_state.user_id
    batch_id = assignment.get("batch_id", "")

    task_pool = build_task_pool(data)
    completed = load_completed_task_ids(user_id, include_skipped=False)
    handled = load_completed_task_ids(user_id, include_skipped=True)

    override_task_id = st.session_state.get("override_task_id")
    if override_task_id:
        task = get_task_by_id(task_pool, override_task_id)
        if task is None:
            st.session_state.override_task_id = None
            task = get_next_unfinished_task(task_pool, handled)
    else:
        task = get_next_unfinished_task(task_pool, handled)

    st.title("Part 1 Position Survey")
    st.markdown(
        f"<div class='meta-text'>Completed tasks: {len(completed)} / {len(task_pool)}</div>",
        unsafe_allow_html=True,
    )

    if task is not None:
        current_position = get_task_position(task_pool, task)
        st.markdown(
            f"<div class='meta-text'>Current task: {current_position} / {len(task_pool)}</div>",
            unsafe_allow_html=True,
        )

    if not task_pool:
        st.error("No valid candidate pairs were found. Please check whether candidate fields are present and non-empty.")
        st.stop()

    if task is None:
        st.success("All candidate-pair tasks have been completed.")
        st.stop()

    if st.button(
        "Back to previous task",
        use_container_width=True,
        disabled=not bool(st.session_state.get("task_history", [])),
    ):
        go_back_to_previous_task()

    display_task = prepare_display_task(task)

    render_previous_context(display_task.get("context", ""), display_task.get("turn", 1))

    st.markdown(f"### Question\n{display_task['question']}")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Version A")
        with st.container(border=True):
            render_candidate_text(display_task["left_text"])

    with col2:
        st.subheader("Version B")
        with st.container(border=True):
            render_candidate_text(display_task["right_text"])

    choices = {}
    for q_key, label in zip(Q_KEYS, Q_LABELS):
        st.markdown(
            f"<p style='font-size:18px; font-weight:600; margin: 0.5rem 0 0.2rem 0;'>{label}</p>",
            unsafe_allow_html=True,
        )
        choices[q_key] = st.radio(
            label,
            Q_OPTIONS,
            index=None,
            key=f"{q_key}_{display_task['task_id']}",
            label_visibility="collapsed",
            horizontal=True,
        )

    col_submit, col_spacer, col_skip = st.columns([1, 0.5, 1])
    with col_submit:
        submit_clicked = st.button("Submit", use_container_width=True)
    with col_skip:
        skip_clicked = st.button("Skip", use_container_width=True)

    if submit_clicked:
        if not all(choices.values()):
            st.warning("Please answer all 5 questions before submitting.")
            st.stop()

        save_result(display_task, user_id.strip(), batch_id, choices)

        if not st.session_state.get("override_task_id"):
            history = st.session_state.get("task_history", [])
            if not history or history[-1] != display_task["task_id"]:
                history.append(display_task["task_id"])
            st.session_state.task_history = history

        st.session_state.override_task_id = None
        reset_choice_state(display_task["task_id"])

        order_key = f"display_order_{display_task['task_id']}"
        if order_key in st.session_state:
            del st.session_state[order_key]

        st.success("Saved!")
        st.rerun()

    if skip_clicked:
        save_result(display_task, user_id.strip(), batch_id, choices, skipped=True)

        if not st.session_state.get("override_task_id"):
            history = st.session_state.get("task_history", [])
            if not history or history[-1] != display_task["task_id"]:
                history.append(display_task["task_id"])
            st.session_state.task_history = history

        st.session_state.override_task_id = None
        reset_choice_state(display_task["task_id"])

        order_key = f"display_order_{display_task['task_id']}"
        if order_key in st.session_state:
            del st.session_state[order_key]

        st.info("Skipped.")
        st.rerun()


if __name__ == "__main__":
    main()