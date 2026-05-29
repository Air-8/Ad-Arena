import streamlit as st
import random
import secrets
import os
import re
import html
import pandas as pd

st.set_page_config(
    page_title="Part 1 Position Survey",
    layout="wide",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Put part1_merged_new_candidates.xlsx in the same folder as this .py file.
DATA_PATH = os.getenv(
    "DATA_PATH",
    os.path.join(BASE_DIR, "part1_merged_new_candidates.xlsx")
)

SUPABASE_TABLE = "part1_results"
SURVEY_PREFIX = "merged"

# Each row becomes one comparison task: candidate2_new vs candidate4_new.
# The attention-check answer for each version comes from the matching *_ad_llm column.
SELECTED_CANDIDATE_FIELD_SPECS = [
    (
        "candidate_2",
        [
            "candidate2",
            "candidate_2",
            "Candidate2",
            "Candidate_2",
        ],
    ),
    (
        "candidate_4",
        [
            "candidate4",
            "candidate_4",
            "Candidate4",
            "Candidate_4",
        ],
    ),
]

ATTENTION_AD_FIELD_SPECS = {
    "candidate_2": [
        "candidate2_ad_llm",
        "candidate_2_ad_llm",
        "Candidate2_ad_llm",
        "Candidate_2_ad_llm",
    ],
    "candidate_4": [
        "candidate4_ad_llm",
        "candidate_4_ad_llm",
        "Candidate4_ad_llm",
        "Candidate_4_ad_llm",
    ],
}

Q_LABELS = [
    "Q1. Naturalness of flow — Which version reads more smoothly and feels less disruptive?",
    "Q2. Helpfulness — Which version better answers the user's question?",
    "Q3. Placement — Which version presents the advertisement earlier in the response?",
    "Q4. Appropriateness — Which version feels more trustworthy and less manipulative?",
    "Q5. Overall preference — All things considered, which version is preferred?",
    "Q6. Ad noticeability — In which version does the advertisement stand out more?"
]

Q_KEYS = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]
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


def make_assignment(user_id):
    """No assignments.csv is needed. Each entered User ID receives the full Excel dataset."""
    return {
        "user_id": str(user_id).strip(),
        "part": "part1",
        "condition": "",
        "batch_id": "merged_full",
        "start_row": 0,
        "end_row": None,
    }


@st.cache_data
def load_data(path):
    samples = []

    def clean_value(value):
        if pd.isna(value):
            return ""
        return str(value)

    def normalize_row(row):
        return {str(k).strip(): clean_value(v) for k, v in row.items()}

    def to_sample(row, row_idx):
        sample_id = row.get("id") or row.get("conversation_id") or f"row_{row_idx}"
        sample = {
            "id": str(sample_id),
            "question": row.get("question", ""),
            "context": row.get("context", ""),
            "turn": row.get("turn", ""),
            "matched_ad": row.get("matched_ad", ""),
            "introduction": row.get("introduction", ""),
        }
        for canonical_field, possible_names in SELECTED_CANDIDATE_FIELD_SPECS:
            sample[canonical_field] = first_nonempty(row, possible_names)
            sample[f"{canonical_field}_attention_ad"] = first_nonempty(
                row,
                ATTENTION_AD_FIELD_SPECS.get(canonical_field, []),
            )
        return sample

    if not os.path.exists(path):
        return samples, "none"

    try:
        if path.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(path)
        else:
            df = pd.read_csv(path)
    except Exception as e:
        st.error(f"Failed to read data file: {path}. Error: {e}")
        st.stop()

    for row_idx, row in enumerate(df.to_dict(orient="records")):
        samples.append(to_sample(normalize_row(row), row_idx))

    return samples, "local"


@st.cache_data
def build_task_pool(samples):
    """Create one fixed comparison task per Excel row."""
    task_pool = []

    for sample in samples:
        field_a, _ = SELECTED_CANDIDATE_FIELD_SPECS[0]
        field_b, _ = SELECTED_CANDIDATE_FIELD_SPECS[1]
        text_a = str(sample.get(field_a, "")).strip()
        text_b = str(sample.get(field_b, "")).strip()

        ad_a = str(sample.get(f"{field_a}_attention_ad", "")).strip()
        ad_b = str(sample.get(f"{field_b}_attention_ad", "")).strip()

        # Rows without the new rewritten candidate text or its matching rewritten ad
        # cannot be used for the new attention-check logic.
        if not text_a or not text_b or not ad_a or not ad_b:
            continue

        task_id = f"{sample['id']}__{field_a}_vs_{field_b}"
        task_pool.append(
            {
                "task_id": task_id,
                "sample_id": sample["id"],
                "question": sample.get("question", ""),
                "context": sample.get("context", ""),
                "turn": sample.get("turn", ""),
                "matched_ad": sample.get("matched_ad", ""),
                "introduction": sample.get("introduction", ""),
                "field_a": field_a,
                "field_b": field_b,
                "text_a": text_a,
                "text_b": text_b,
                "ad_a": ad_a,
                "ad_b": ad_b,
            }
        )

    return task_pool


def normalize_numbered_items(raw: str) -> str:
    """Only insert line breaks before numbered markers like 1. 2. 3.

    It does not insert line breaks after English periods.
    """
    raw = str(raw or "")
    raw = re.sub(r"(?<!\n)\s+(#{1,6})\s+", r"\n\1 ", raw)
    raw = re.sub(r"(?<!^)(?<!\n)\s+(\d+\.\s+)", r"\n\1", raw)
    return raw.strip()


def render_candidate_text(text: str):
    """Render candidate text while extracting [sponsored ...] into a separate paragraph."""
    if not text:
        st.write("")
        return

    def normalize_markdown_headings(raw: str) -> str:
        raw = normalize_numbered_items(raw)

        def _shift_heading(match):
            hashes = match.group(1)
            level = min(len(hashes) + 3, 6)
            return "#" * level + " "

        return re.sub(r"(?m)^(#{1,6})\s+", _shift_heading, raw)

    pattern = re.compile(r"\[(?i:sponsor(?:ed)?)\s+(.*?)\]", flags=re.DOTALL)
    cursor = 0

    for match in pattern.finditer(str(text)):
        normal_part = str(text)[cursor:match.start()].strip()
        if normal_part:
            st.markdown(normalize_markdown_headings(normal_part))

        sponsored_content = re.sub(r"\s+", " ", match.group(1)).strip()
        if sponsored_content:
            st.markdown(
                f"<p style='color:#8A8A8A;'><strong>Sponsored:</strong> "
                f"{html.escape(sponsored_content)}</p>",
                unsafe_allow_html=True,
            )

        cursor = match.end()

    tail = str(text)[cursor:].strip()
    if tail:
        st.markdown(normalize_markdown_headings(tail))


def extract_sponsored_text(text: str):
    if not text:
        return ""

    pattern = re.compile(r"\[(?i:sponsor(?:ed)?)\s+(.*?)\]", flags=re.DOTALL)
    match = pattern.search(str(text))
    if not match:
        return ""

    return re.sub(r"\s+", " ", match.group(1)).strip()


def normalize_for_match(text: str):
    return re.sub(r"[^a-zA-Z0-9]+", " ", str(text or "").lower()).strip()


def normalize_text_for_attention_display(text: str):
    """Keep original order, but make inline numbered lists readable."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    return normalize_numbered_items(text)


def is_sentence_boundary(text: str, idx: int):
    """Return True if text[idx] is an English sentence-ending punctuation mark."""
    ch = text[idx]
    if ch not in ".!?":
        return False

    next_char = text[idx + 1] if idx + 1 < len(text) else ""
    if next_char and not next_char.isspace():
        return False

    prev_char = text[idx - 1] if idx - 1 >= 0 else ""

    # Do not split numeric list markers such as "1." or numbers such as "4.5".
    if ch == "." and prev_char.isdigit():
        return False

    left = text[:idx].lower()
    token_match = re.search(r"([a-z](?:\.[a-z]){0,3}|[a-z]+)$", left)
    token = token_match.group(1) if token_match else ""

    common_abbreviations = {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "e.g",
        "i.e",
        "u.s",
        "u.k",
        "a.m",
        "p.m",
        "fig",
        "no",
        "inc",
        "ltd",
        "co",
    }
    if ch == "." and token in common_abbreviations:
        return False

    return True


def _split_plain_attention_text(text: str, start_idx: int = 0):
    """Split non-sponsored text into clickable units.

    Numbered lines like "1. Bridesmaids (2011)" are treated as one unit.
    Other prose is split by English sentence punctuation, but visual line breaks
    are only preserved from the source or inserted before numbered markers.
    """
    parts = []
    sentence_idx = start_idx

    lines = str(text).split("\n")
    for line_no, line in enumerate(lines):
        if line_no > 0:
            parts.append(
                {"text": "\n", "clickable": False, "idx": None, "sponsored": False}
            )

        if not line:
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            item = line.strip()
            if item:
                parts.append(
                    {
                        "text": item,
                        "clickable": True,
                        "idx": sentence_idx,
                        "sponsored": False,
                    }
                )
                sentence_idx += 1
            continue

        sentence_start = None
        last_consumed = 0

        for idx, ch in enumerate(line):
            if sentence_start is None and not ch.isspace():
                if last_consumed < idx:
                    parts.append(
                        {
                            "text": line[last_consumed:idx],
                            "clickable": False,
                            "idx": None,
                            "sponsored": False,
                        }
                    )
                sentence_start = idx

            if sentence_start is not None and is_sentence_boundary(line, idx):
                parts.append(
                    {
                        "text": line[sentence_start:idx + 1],
                        "clickable": True,
                        "idx": sentence_idx,
                        "sponsored": False,
                    }
                )
                sentence_idx += 1
                sentence_start = None
                last_consumed = idx + 1

        if sentence_start is not None:
            tail = line[sentence_start:]
            if tail.strip():
                parts.append(
                    {
                        "text": tail,
                        "clickable": True,
                        "idx": sentence_idx,
                        "sponsored": False,
                    }
                )
                sentence_idx += 1
            last_consumed = len(line)

        if last_consumed < len(line):
            parts.append(
                {
                    "text": line[last_consumed:],
                    "clickable": False,
                    "idx": None,
                    "sponsored": False,
                }
            )

    return parts, sentence_idx


def split_into_sentence_parts(text: str):
    """Split attention-check text into clickable units.

    Sponsored blocks are rendered as clickable "Sponsored: ..." units.
    Numbered items keep the number attached to the item.
    """
    text = normalize_text_for_attention_display(text)
    if not text:
        return []

    parts = []
    sentence_idx = 0
    cursor = 0
    sponsor_pattern = re.compile(r"\[(?i:sponsor(?:ed)?)\s+(.*?)\]", flags=re.DOTALL)

    for match in sponsor_pattern.finditer(text):
        normal_part = text[cursor:match.start()]
        normal_parts, sentence_idx = _split_plain_attention_text(normal_part, sentence_idx)
        parts.extend(normal_parts)

        sponsored_content = re.sub(r"\s+", " ", match.group(1)).strip()
        if sponsored_content:
            parts.append(
                {
                    "text": sponsored_content,
                    "display_text": f"Sponsored: {sponsored_content}",
                    "clickable": True,
                    "idx": sentence_idx,
                    "sponsored": True,
                }
            )
            sentence_idx += 1

        cursor = match.end()

    tail = text[cursor:]
    tail_parts, sentence_idx = _split_plain_attention_text(tail, sentence_idx)
    parts.extend(tail_parts)

    return [
        part
        for part in parts
        if str(part.get("text", "")).strip() or part.get("text") == "\n"
    ]


def is_correct_attention_choice(selected_text: str, sponsored_text: str):
    selected = normalize_for_match(selected_text)
    sponsored = normalize_for_match(sponsored_text)

    if not selected or not sponsored:
        return False

    if sponsored in selected or selected in sponsored:
        return True

    selected_words = set(selected.split())
    sponsored_words = set(sponsored.split())
    if not sponsored_words:
        return False

    return len(selected_words & sponsored_words) / len(sponsored_words) >= 0.55


def reset_attention_state(task_id):
    keys = [
        f"attention_passed_{task_id}",
        f"attention_left_passed_{task_id}",
        f"attention_right_passed_{task_id}",
        f"attention_selected_left_{task_id}",
        f"attention_selected_right_{task_id}",
        f"attention_warning_{task_id}",
    ]
    for key in keys:
        if key in st.session_state:
            del st.session_state[key]


def render_attention_inline_css():
    st.html(
        """
        <style>
        div[class*="st-key-attn_sentence_"],
        div[class*="st-key-attn_sponsored_"] {
            display: inline !important;
        }

        div[class*="st-key-attn_sentence_"] button,
        div[class*="st-key-attn_sponsored_"] button {
            display: inline !important;
            width: auto !important;
            min-height: 0 !important;
            height: auto !important;
            padding: 0 2px !important;
            margin: 0 2px 0 0 !important;
            border: none !important;
            background: transparent !important;
            color: inherit !important;
            box-shadow: none !important;
            text-align: left !important;
            vertical-align: baseline !important;
            font: inherit !important;
            line-height: 1.55 !important;
            white-space: normal !important;
        }

        div[class*="st-key-attn_sentence_"] button p,
        div[class*="st-key-attn_sponsored_"] button p {
            display: inline !important;
            font-size: 1rem !important;
            line-height: 1.55 !important;
            margin: 0 !important;
            padding: 0 !important;
        }

        div[class*="st-key-attn_sentence_"] button:hover,
        div[class*="st-key-attn_sentence_"] button:focus,
        div[class*="st-key-attn_sentence_"] button:active,
        div[class*="st-key-attn_sponsored_"] button:hover,
        div[class*="st-key-attn_sponsored_"] button:focus,
        div[class*="st-key-attn_sponsored_"] button:active {
            border: none !important;
            background-color: #F1F5F9 !important;
            color: inherit !important;
            box-shadow: none !important;
            text-decoration: underline !important;
        }

        div[class*="st-key-attn_sponsored_"] button,
        div[class*="st-key-attn_sponsored_"] button p {
            color: #8A8A8A !important;
            font-weight: 600 !important;
        }

        .attention-response-block {
            line-height: 1.55;
            font-size: 1rem;
        }
        </style>
        """
    )


def safe_button_key_part(value: str):
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "task"))[:120]


def render_clickable_attention_text(text: str, side: str, task_id: str):
    clicked_sentence = None
    safe_task = safe_button_key_part(task_id)

    st.markdown("<div class='attention-response-block'>", unsafe_allow_html=True)

    for part in split_into_sentence_parts(text):
        part_text = part.get("text", "")
        if not part_text:
            continue

        if part.get("clickable"):
            idx = part["idx"]
            display_text = part.get("display_text", part_text).strip()
            button_prefix = "attn_sponsored" if part.get("sponsored") else "attn_sentence"

            if st.button(
                display_text,
                key=f"{button_prefix}_{side}_{safe_task}_{idx}",
            ):
                clicked_sentence = part_text.strip()
        else:
            if "\n" in part_text:
                st.markdown("<br>", unsafe_allow_html=True)
            elif part_text.strip():
                st.markdown(html.escape(part_text), unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
    return clicked_sentence


def render_attention_check(display_task):
    """Block the formal survey questions until both versions pass the attention check."""
    task_id = display_task["task_id"]
    passed_key = f"attention_passed_{task_id}"
    left_passed_key = f"attention_left_passed_{task_id}"
    right_passed_key = f"attention_right_passed_{task_id}"
    selected_left_key = f"attention_selected_left_{task_id}"
    selected_right_key = f"attention_selected_right_{task_id}"
    warning_key = f"attention_warning_{task_id}"

    if st.session_state.get(passed_key):
        return

    left_ad = str(display_task.get("left_attention_ad", "")).strip()
    right_ad = str(display_task.get("right_attention_ad", "")).strip()

    if not left_ad or not right_ad:
        st.error("Attention-check ad text is missing for this task. Please check candidate2_ad_llm and candidate4_ad_llm in the data file.")
        st.stop()

    st.divider()
    st.markdown(
        "Before answering the questions, please click the sentence that contains the advertisement "
        "in **both** Version A and Version B. You must identify the ad sentence in both versions "
        "before continuing."
    )

    render_attention_inline_css()

    clicked_side = None
    clicked_text = None

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("**Version A**")
        with st.container(border=True):
            left_clicked = render_clickable_attention_text(
                display_task.get("left_text", ""),
                "left",
                task_id,
            )
            if left_clicked:
                clicked_side = "left"
                clicked_text = left_clicked

    with col_right:
        st.markdown("**Version B**")
        with st.container(border=True):
            right_clicked = render_clickable_attention_text(
                display_task.get("right_text", ""),
                "right",
                task_id,
            )
            if right_clicked:
                clicked_side = "right"
                clicked_text = right_clicked

    if clicked_text:
        if clicked_side == "left":
            sponsored_text = left_ad
            side_passed_key = left_passed_key
            side_selected_key = selected_left_key
            side_label = "Version A"
        else:
            sponsored_text = right_ad
            side_passed_key = right_passed_key
            side_selected_key = selected_right_key
            side_label = "Version B"

        st.session_state[side_selected_key] = clicked_text

        if is_correct_attention_choice(clicked_text, sponsored_text):
            st.session_state[side_passed_key] = True
            if warning_key in st.session_state:
                del st.session_state[warning_key]
        else:
            st.session_state[warning_key] = "Please select the correct advertisement sentence in both versions."

        if st.session_state.get(left_passed_key) and st.session_state.get(right_passed_key):
            st.session_state[passed_key] = True
            if warning_key in st.session_state:
                del st.session_state[warning_key]
            st.rerun()

    if st.session_state.get(warning_key):
        st.warning(st.session_state[warning_key])

    st.stop()


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
            exchanges.append(
                {
                    "user": user_text,
                    "assistant": assistant_text,
                }
            )

    return exchanges


def render_previous_context(context_raw: str, turn, introduction: str = ""):
    try:
        turn_num = int(turn)
    except Exception:
        turn_num = 1

    if turn_num <= 1:
        return

    exchanges = parse_context(context_raw)
    if not exchanges:
        return

    intro = str(introduction or "").strip()
    if intro:
        intro = intro.rstrip(".?!") + "."
        st.markdown(f"{intro} The following is the previous conversation for your reference.")
    else:
        st.markdown("The following is the previous conversation for your reference.")

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


def init_display_orders(task_pool, user_id, batch_id):
    """Create a balanced randomized left/right assignment for the current user batch."""
    task_ids = [task["task_id"] for task in task_pool]
    map_key = (
        f"display_order_map_{safe_button_key_part(user_id)}_"
        f"{safe_button_key_part(batch_id)}_{len(task_ids)}"
    )

    current_map = st.session_state.get(map_key)
    if isinstance(current_map, dict) and set(current_map.keys()) == set(task_ids):
        st.session_state["display_order_map_key"] = map_key
        return

    orders = ["AB", "BA"] * (len(task_ids) // 2)
    if len(task_ids) % 2:
        orders.append(secrets.choice(["AB", "BA"]))
    random.shuffle(orders)

    st.session_state[map_key] = dict(zip(task_ids, orders))
    st.session_state["display_order_map_key"] = map_key


def prepare_display_task(task):
    """Keep the pair fixed, but randomize whether field_a or field_b appears on the left."""
    map_key = st.session_state.get("display_order_map_key")
    order_map = st.session_state.get(map_key, {}) if map_key else {}
    display_order = order_map.get(task["task_id"], secrets.choice(["AB", "BA"]))

    if display_order == "AB":
        return {
            **task,
            "left_field": task["field_a"],
            "left_text": task["text_a"],
            "left_attention_ad": task.get("ad_a", ""),
            "right_field": task["field_b"],
            "right_text": task["text_b"],
            "right_attention_ad": task.get("ad_b", ""),
            "display_order": display_order,
        }

    return {
        **task,
        "left_field": task["field_b"],
        "left_text": task["text_b"],
        "left_attention_ad": task.get("ad_b", ""),
        "right_field": task["field_a"],
        "right_text": task["text_a"],
        "right_attention_ad": task.get("ad_a", ""),
        "display_order": display_order,
    }


def choice_to_machine_value(choice):
    if choice == "Version A":
        return "left"
    if choice == "Version B":
        return "right"
    if choice == "Tie":
        return "tie"
    return ""


def get_ad_start_position(text: str, matched_ad: str = ""):
    text = str(text or "")
    match = re.search(r"\[(?i:sponsor(?:ed)?)\s+.*?\]", text, flags=re.DOTALL)
    if match:
        return match.start()

    normalized_text = normalize_for_match(text)
    normalized_ad = normalize_for_match(matched_ad)

    if normalized_text and normalized_ad:
        pos = normalized_text.find(normalized_ad)
        if pos >= 0:
            return pos

    return None


def get_ad_position_answer(display_task):
    left_pos = get_ad_start_position(
        display_task.get("left_text", ""),
        display_task.get("left_attention_ad", "") or display_task.get("matched_ad", ""),
    )
    right_pos = get_ad_start_position(
        display_task.get("right_text", ""),
        display_task.get("right_attention_ad", "") or display_task.get("matched_ad", ""),
    )

    if left_pos is None and right_pos is None:
        return "tie"
    if left_pos is None:
        return "right"
    if right_pos is None:
        return "left"
    if left_pos == right_pos:
        return "tie"
    return "left" if left_pos < right_pos else "right"


def save_result(display_task, user_id, batch_id, choices, skipped: bool = False):
    payload = {
        "user_id": user_id,
        "batch_id": batch_id,
        "task_id": display_task["task_id"],
        "sample_id": display_task["sample_id"],
        "left_field": display_task["left_field"],
        "right_field": display_task["right_field"],
        "display_order": display_task.get("display_order", ""),
        "Q1_choice": "" if skipped else choice_to_machine_value(choices.get("Q1", "")),
        "Q2_choice": "" if skipped else choice_to_machine_value(choices.get("Q2", "")),
        "Q3_choice": "" if skipped else choice_to_machine_value(choices.get("Q3", "")),
        "Q4_choice": "" if skipped else choice_to_machine_value(choices.get("Q4", "")),
        "Q5_choice": "" if skipped else choice_to_machine_value(choices.get("Q5", "")),
        "Q6_choice": "" if skipped else choice_to_machine_value(choices.get("Q6", "")),
        "Q6_correct_answer": "" if skipped else get_ad_position_answer(display_task),
        "Q6_is_correct": None
        if skipped
        else (
            choice_to_machine_value(choices.get("Q6", ""))
            == get_ad_position_answer(display_task)
        ),
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
    required_prefix = SURVEY_PREFIX + "_"
    st.markdown(f"Please enter your User ID. It must start with `{required_prefix}`.")

    user_id = st.text_input("User ID")

    if st.button("Continue", use_container_width=True):
        raw_user_id = user_id.strip()

        if not raw_user_id:
            st.warning("Please enter your User ID.")
            st.stop()

        if not raw_user_id.startswith(required_prefix):
            st.warning(f"Please enter a valid User ID starting with {required_prefix}")
            st.stop()

        assignment = make_assignment(raw_user_id)

        st.session_state.user_id = raw_user_id
        st.session_state.assignment = assignment
        st.session_state.task_history = []
        st.session_state.override_task_id = None
        st.session_state.page = "calibration"
        st.rerun()


def show_calibration_page():
    st.title("Response Comparison Guide")

    st.markdown(
        """
You will compare two versions of the same AI response. The two versions may differ in where an advertisement appears. For each question, choose **Version A**, **Version B**, or **Tie**.

**Naturalness of flow**  
Choose the version that reads more smoothly and feels less disruptive. A natural version should fit the surrounding response and not feel abruptly inserted.

**Helpfulness**  
Choose the version that better answers the user's question. Focus on usefulness and relevance, not on whether you personally like the advertisement.

**Ad noticeability**  
Choose the version where the advertisement stands out more. This does not mean the version is better or worse; it only asks which advertisement is easier to notice.

**Appropriateness**  
Choose the version that feels more trustworthy and less manipulative. Consider whether the advertisement feels suitable, transparent, and not overly pushy.

**Overall preference**  
Choose the version you prefer all things considered.

**Your answers are checked for consistency and quality. The final reward depends on careful reading and agreement with the main response pattern. Too many inconsistent or careless answers will reduce the reward according to the study rules.**

If the two versions are about equally good on a dimension, choose **Tie**.
"""
    )

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
    reset_attention_state(previous_task_id)
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

    data, data_source = load_data(DATA_PATH)
    if not data:
        st.error(f"No data found. Please check: {DATA_PATH}")
        st.stop()

    start_row = assignment.get("start_row", 0)
    end_row = assignment.get("end_row")
    data = data[start_row:end_row] if end_row is not None else data[start_row:]

    if data_source == "local":
        data_source_msg = f"Data source: Local file ({DATA_PATH})"
    else:
        data_source_msg = "Data source: Unknown"

    if st.session_state.get("_last_data_source_msg") != data_source_msg:
        print(f"[Ad-Arena] {data_source_msg}")
        st.session_state["_last_data_source_msg"] = data_source_msg

    user_id = st.session_state.user_id
    batch_id = assignment.get("batch_id", "")

    task_pool = build_task_pool(data)
    init_display_orders(task_pool, user_id, batch_id)

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
        st.error(
            "No valid candidate pairs were found. Please check whether candidate2_new, "
            "candidate4_new, candidate2_ad_llm, and candidate4_ad_llm are present and non-empty."
        )
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

    render_previous_context(
        display_task.get("context", ""),
        display_task.get("turn", 1),
        display_task.get("introduction", ""),
    )

    st.markdown(f"### Question\n{display_task['question']}")

    render_attention_check(display_task)

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
            st.warning("Please answer all questions before submitting.")
            st.stop()

        save_result(display_task, user_id.strip(), batch_id, choices)

        if not st.session_state.get("override_task_id"):
            history = st.session_state.get("task_history", [])
            if not history or history[-1] != display_task["task_id"]:
                history.append(display_task["task_id"])
            st.session_state.task_history = history

        st.session_state.override_task_id = None
        reset_choice_state(display_task["task_id"])
        reset_attention_state(display_task["task_id"])

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
        reset_attention_state(display_task["task_id"])

        st.info("Skipped.")
        st.rerun()


if __name__ == "__main__":
    main()