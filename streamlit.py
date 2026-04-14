import streamlit as st
import random
import csv
from datetime import datetime
import os
import re
import io
from urllib.request import urlopen
from urllib.error import URLError
from supabase import create_client, Client
from streamlit.errors import StreamlitSecretNotFoundError

DATA_PATH = os.path.join("data", "stage4_reorganized_top4_thr0_8_pairwise.csv")


def get_secret_safe(key: str, default: str = "") -> str:
    try:
        return st.secrets.get(key, default)
    except StreamlitSecretNotFoundError:
        return default


SUPABASE_URL = os.getenv("SUPABASE_URL") or get_secret_safe("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or get_secret_safe("SUPABASE_KEY", "")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE") or get_secret_safe("SUPABASE_TABLE", "results")
DATA_CSV_URL = (
    os.getenv("DATA_CSV_URL")
    or get_secret_safe("DATA_CSV_URL", "")
)


@st.cache_data
def load_data(path, csv_url=""):
    samples = []

    # 优先从 URL 读取（适合云部署）
    if csv_url:
        try:
            with urlopen(csv_url) as resp:
                content = resp.read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                samples.append(
                    {
                        "id": row.get("id", ""),
                        "question": row.get("question", ""),
                        "candidate_1": row.get("candidate_1", ""),
                        "candidate_2": row.get("candidate_2", ""),
                    }
                )
            if samples:
                return samples, "cloud"
        except (URLError, UnicodeDecodeError, csv.Error):
            # URL 失败则回退本地文件
            pass

    if not os.path.exists(path):
        return samples, "none"

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            samples.append(
                {
                    "id": row.get("id", ""),
                    "question": row.get("question", ""),
                    "candidate_1": row.get("candidate_1", ""),
                    "candidate_2": row.get("candidate_2", ""),
                }
            )
    return samples, "local"


def render_candidate_text(text: str):
    """Render candidate text while extracting [sponsored ...] into a separate paragraph."""
    if not text:
        st.write("")
        return

    def normalize_markdown_headings(raw: str) -> str:
        # 先把行内标题（如：文字 #### 标题）断到新行，便于按 markdown 标题解析
        raw = re.sub(r"(?<!\n)\s+(#{1,6})\s+", r"\n\1 ", raw)

        # 保留 markdown 标题语义，但整体降三级，避免标题过大（# -> ####）
        def _shift_heading(match):
            hashes = match.group(1)
            level = min(len(hashes) + 3, 6)
            return "#" * level + " "

        # 仅处理行首标题（上面已将行内标题改写为行首）
        return re.sub(r"(?m)^(#{1,6})\s+", _shift_heading, raw)

    pattern = re.compile(r"\[(?i:sponsored)\s+(.*?)\]", flags=re.DOTALL)
    cursor = 0

    for match in pattern.finditer(text):
        # 前置普通段落
        normal_part = text[cursor:match.start()].strip()
        if normal_part:
            st.markdown(normalize_markdown_headings(normal_part))

        # sponsored 段落（独立显示，不改颜色）
        sponsored_content = match.group(1).strip()
        if sponsored_content:
            st.markdown(
                f"<p style='color:#8A8A8A;'><strong>Sponsored:</strong> {sponsored_content}</p>",
                unsafe_allow_html=True,
            )

        cursor = match.end()

    # 后置普通段落
    tail = text[cursor:].strip()
    if tail:
        st.markdown(normalize_markdown_headings(tail))


@st.cache_resource
def get_supabase_client() -> Client | None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)


data, data_source = load_data(DATA_PATH, DATA_CSV_URL)

if not data:
    st.error(f"No data found. Please check: {DATA_PATH}")
    st.stop()

if data_source == "cloud":
    data_source_msg = f"Data source: Cloud URL ({DATA_CSV_URL})"
elif data_source == "local":
    data_source_msg = f"Data source: Local file ({DATA_PATH})"
else:
    data_source_msg = "Data source: Unknown"

# 仅在终端打印来源，不在问卷页面显示；并避免每次 rerun 重复刷屏
if st.session_state.get("_last_data_source_msg") != data_source_msg:
    print(f"[Ad-Arena] {data_source_msg}")
    st.session_state["_last_data_source_msg"] = data_source_msg

# 初始化 session（避免每次刷新都换题）
if "current_sample" not in st.session_state:
    st.session_state.current_sample = random.choice(data)

if "current_reverse" not in st.session_state:
    st.session_state.current_reverse = random.randint(0, 1)

sample = st.session_state.current_sample
reverse_flag = int(st.session_state.current_reverse)

if reverse_flag == 1:
    shown_candidate_1 = sample["candidate_2"]
    shown_candidate_2 = sample["candidate_1"]
else:
    shown_candidate_1 = sample["candidate_1"]
    shown_candidate_2 = sample["candidate_2"]

supabase_client = get_supabase_client()
if supabase_client is None:
    st.error("Supabase is not configured. Please set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

user_id = st.text_input("User ID", placeholder="e.g. user_001")

st.markdown("<p style='font-size:28px; font-weight:700;'>Assume you are talking with a generative AI assistant.</p>", unsafe_allow_html=True)
st.markdown(f"### Question\n{sample['question']}")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Candidate 1")
    render_candidate_text(shown_candidate_1)

with col2:
    st.subheader("Candidate 2")
    render_candidate_text(shown_candidate_2)

st.markdown(
    "<p style='font-size:18px; font-weight:600; margin: 0.5rem 0 0.2rem 0;'>Q1. Which candidate is better?</p>",
    unsafe_allow_html=True,
)

choice_1 = st.radio(
    "Q1. Which candidate is better?",
    ["Candidate 1", "Candidate 2", "Tie"],
    index=None,
    key=f"q1_{sample['id']}",
    label_visibility="collapsed",
)

st.markdown(
    "<p style='font-size:18px; font-weight:600; margin: 0.5rem 0 0.2rem 0;'>Q2. How strong is your preference?</p>",
    unsafe_allow_html=True,
)

choice_2 = st.radio(
    "Q2. How strong is your preference?",
    ["Strong", "Medium", "Weak"],
    index=None,
    key=f"q2_{sample['id']}",
    label_visibility="collapsed",
)

st.markdown(
    "<p style='font-size:18px; font-weight:600; margin: 0.5rem 0 0.2rem 0;'>Q3. How confident are you in this judgement?</p>",
    unsafe_allow_html=True,
)

choice_3 = st.radio(
    "Q3. How confident are you in this judgement?",
    ["Very confident", "Confident", "Unsure", "Not confident"],
    index=None,
    key=f"q3_{sample['id']}",
    label_visibility="collapsed",
)

# ====== 保存函数 ======
def save_result(sample_id, user_id, choice):
    payload = {
        "sample_id": sample_id,
        "user_id": user_id,
        "choice": choice,
        "reverse": reverse_flag,
        "timestamp": datetime.utcnow().isoformat(),
    }

    supabase_client.table(SUPABASE_TABLE).insert(payload).execute()


# ====== 提交按钮 ======
if st.button("Submit"):
    if not user_id.strip():
        st.warning("Please enter your User ID before submitting.")
        st.stop()

    if not all([choice_1, choice_2, choice_3]):
        st.warning("Please answer all 3 questions before submitting.")
        st.stop()

    combined_choice = f"Q1={choice_1} | Q2={choice_2} | Q3={choice_3}"
    save_result(sample["id"], user_id.strip(), combined_choice)

    st.success("Saved!")

    # 换下一题
    st.session_state.current_sample = random.choice(data)
    st.session_state.current_reverse = random.randint(0, 1)

    # 刷新页面（显示新题）
    st.rerun()