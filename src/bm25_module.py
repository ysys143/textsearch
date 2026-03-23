"""
bm25_module.py

BM25 관련 코드를 노트북(0521.ipynb)에서 추출하여 독립 실행 가능한 모듈로 리팩토링.
"""

# --- 1. 임포트 ---

import os
import re
import math
import json
from collections import defaultdict
from typing import Any, Callable, Dict, List, Tuple

try:
    import numpy as np
except ImportError:
    print("[WARNING] numpy not installed. Array features disabled.")
    np = None  # type: ignore[assignment]

try:
    import psycopg2
except ImportError:
    print("[WARNING] psycopg2 not installed. Database features disabled.")
    psycopg2 = None  # type: ignore[assignment]

try:
    from pgvector.psycopg2 import register_vector, SparseVector
except ImportError:
    print("[WARNING] pgvector not installed. SparseVector features disabled.")
    register_vector = None
    SparseVector = None

_KIWI_INSTANCES: dict = {}

_MeCab = None  # type: ignore[assignment]
try:
    from mecab import MeCab as _MeCab  # type: ignore[import]
    _MECAB_AVAILABLE = True
except ImportError:
    _MECAB_AVAILABLE = False
    print("[WARNING] python-mecab-ko not installed. Will fall back to Okt if available.")

_Okt = None  # type: ignore[assignment]
try:
    from konlpy.tag import Okt as _Okt  # type: ignore[import]
    _OKT_AVAILABLE = True
except ImportError:
    _OKT_AVAILABLE = False
    print("[WARNING] konlpy not installed. Korean tokenization unavailable.")


# --- 2. DB 연결 설정 ---

# 환경변수 DATABASE_URL 또는 개별 설정값으로 구성
# 예시: export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/dev

DB_CONFIG = {
    "dbname": os.getenv("DB_NAME", "dev"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
}


# --- 3. 유틸리티 함수 ---

# Cell 35
def execute_query(query: str, values: Tuple = (), explain: bool = False):
    """
    PostgreSQL 쿼리를 실행하는 범용 헬퍼.
    - SELECT / WITH ... SELECT / SHOW  → fetchall() 결과 반환
    - INSERT / UPDATE / DELETE 등      → commit 후 None 반환
    - explain=True 시 EXPLAIN ANALYZE 결과를 출력하고 반환
    """
    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        if register_vector is not None:
            register_vector(conn)
        cursor = conn.cursor()

        if explain:
            cursor.execute("EXPLAIN ANALYZE " + query, values)
            result = cursor.fetchall()
            for row in result:
                print(row[0])
            return result
        else:
            cursor.execute(query, values)
            normalized = query.strip().lower()
            if normalized.startswith("select"):
                return cursor.fetchall()
            elif re.match(r"^\s*with\s+.*\bselect\b", normalized, re.DOTALL):
                return cursor.fetchall()
            elif normalized.startswith("show"):
                return cursor.fetchall()
            else:
                conn.commit()
                return None
    except Exception as e:
        print(f"[execute_query] Error: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# --- 4. 테이블 초기화 함수 ---

# Cell 24 / Cell 77
def init_text_embedding_table(vector_dim: int = 1536):
    """
    text_embedding 테이블을 초기화한다.
    기존 테이블이 있으면 삭제 후 재생성 (CASCADE 포함).
    """
    execute_query("DROP TABLE IF EXISTS text_embedding CASCADE;")
    execute_query(f"""
        CREATE TABLE text_embedding (
            id SERIAL PRIMARY KEY,
            text TEXT NOT NULL,
            emb VECTOR({vector_dim})
        );
    """)
    print(f"[init_text_embedding_table] text_embedding({vector_dim}d) 생성 완료.")


# Cell 119
def init_inverted_index_table():
    """
    BM25용 역색인 테이블 inverted_index 를 생성한다.
    text_embedding 테이블이 먼저 존재해야 한다.
    """
    execute_query("""
        CREATE TABLE IF NOT EXISTS inverted_index (
            term TEXT,
            doc_id INT,
            term_freq INT,
            doc_length INT,
            PRIMARY KEY (term, doc_id),
            FOREIGN KEY (doc_id) REFERENCES text_embedding(id)
        );
    """)
    print("[init_inverted_index_table] inverted_index 생성 완료.")


# Cell 121
def create_pg_function_index_single_document():
    """
    PostgreSQL 함수 index_single_document() 를 생성/교체한다.
    mecab-ko(textsearch_ko) 기반 'public.korean' 텍스트 검색 설정을 사용한다.
    """
    execute_query("""
        CREATE OR REPLACE FUNCTION index_single_document(p_doc_id INT)
        RETURNS VOID AS $$
        DECLARE
            doc_text TEXT;
        BEGIN
            DELETE FROM inverted_index WHERE doc_id = p_doc_id;

            SELECT text INTO doc_text FROM text_embedding WHERE id = p_doc_id;

            INSERT INTO inverted_index (term, doc_id, term_freq, doc_length)
            SELECT
                term,
                p_doc_id,
                COUNT(*) AS term_freq,
                COUNT(*) OVER () AS doc_length
            FROM (
                SELECT unnest(tsvector_to_array(to_tsvector('public.korean', doc_text))) AS term
            ) AS terms
            GROUP BY term;
        END;
        $$ LANGUAGE plpgsql;
    """)
    print("[create_pg_function_index_single_document] 함수 생성 완료.")


# Cell 122
def create_pg_trigger_update_inverted_index():
    """
    text_embedding 에 INSERT/UPDATE 시 자동으로 역색인을 갱신하는 트리거를 생성한다.
    """
    execute_query("""
        CREATE OR REPLACE FUNCTION trigger_update_inverted_index()
        RETURNS trigger AS $$
        BEGIN
            PERFORM index_single_document(NEW.id);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    execute_query("DROP TRIGGER IF EXISTS trg_update_index ON text_embedding;")
    execute_query("""
        CREATE TRIGGER trg_update_index
        AFTER INSERT OR UPDATE ON text_embedding
        FOR EACH ROW EXECUTE FUNCTION trigger_update_inverted_index();
    """)
    print("[create_pg_trigger_update_inverted_index] 트리거 생성 완료.")


# Cell 125
def create_pg_function_bm25_ranking():
    """
    PostgreSQL 함수 bm25_ranking() 을 생성/교체한다.
    mecab-ko(textsearch_ko) 기반 'public.korean' 텍스트 검색 설정을 사용한다.
    """
    execute_query("""
        CREATE OR REPLACE FUNCTION bm25_ranking(query TEXT, k1 FLOAT DEFAULT 1.2, b FLOAT DEFAULT 0.75)
        RETURNS TABLE(doc_id INT, score FLOAT) AS $$
        DECLARE
            avgdl FLOAT;
            total_docs INT;
        BEGIN
            SELECT AVG(i.doc_length) INTO avgdl FROM inverted_index i;
            SELECT COUNT(DISTINCT i.doc_id) INTO total_docs FROM inverted_index i;

            RETURN QUERY
            SELECT
                i.doc_id,
                SUM(
                    LOG((total_docs - df.df + 0.5) / (df.df + 0.5) + 1) *
                    (i.term_freq * (k1 + 1)) /
                    (i.term_freq + k1 * (1 - b + b * (i.doc_length / avgdl)))
                ) AS score
            FROM inverted_index i
            JOIN (
                SELECT inv.term, COUNT(DISTINCT inv.doc_id) AS df
                FROM inverted_index inv
                WHERE inv.term = ANY(tsvector_to_array(to_tsvector('public.korean', query)))
                GROUP BY inv.term
            ) df ON i.term = df.term
            WHERE i.term = ANY(tsvector_to_array(to_tsvector('public.korean', query)))
            GROUP BY i.doc_id
            ORDER BY score DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)
    print("[create_pg_function_bm25_ranking] 함수 생성 완료.")


# Cell 128
def create_inverted_index_term_index():
    """inverted_index.term 컬럼에 인덱스를 생성한다."""
    execute_query("CREATE INDEX IF NOT EXISTS idx_term ON inverted_index(term);")
    print("[create_inverted_index_term_index] 인덱스 생성 완료.")


def rebuild_inverted_index():
    """
    text_embedding 의 모든 문서에 대해 역색인을 재구성한다.
    Cell 126 참조.
    """
    rows = execute_query("SELECT id FROM text_embedding;") or []
    for (doc_id,) in rows:
        execute_query("SELECT index_single_document(%s);", (doc_id,))
    print(f"[rebuild_inverted_index] {len(rows)}개 문서 색인 완료.")


# --- 5. BM25Embedder 클래스 ---

KIWI_CONTENT_POS = {'NNG', 'NNP', 'NNB', 'VV', 'VA', 'MAG', 'XR', 'SL'}


def _build_tokenizer(tokenizer_name: str):
    """
    tokenizer_name에 따라 형태소 분석기 인스턴스와 morphs 메서드를 반환한다.
    MeCab 우선, 없으면 Okt로 fallback.
    """
    if tokenizer_name == "Mecab":
        if _MECAB_AVAILABLE:
            analyzer = _MeCab()  # type: ignore[misc]
            return analyzer, analyzer.morphs
        elif _OKT_AVAILABLE:
            print("[WARNING] MeCab 없음 → Okt로 fallback.")
            analyzer = _Okt()  # type: ignore[misc]
            return analyzer, analyzer.morphs
        else:
            raise RuntimeError("MeCab도 Okt도 설치되어 있지 않습니다.")
    elif tokenizer_name == "Okt":
        if _OKT_AVAILABLE:
            analyzer = _Okt()  # type: ignore[misc]
            return analyzer, analyzer.morphs
        else:
            raise RuntimeError("Okt(konlpy)가 설치되어 있지 않습니다.")
    elif tokenizer_name.lower() in ("kiwi-cong", "kiwi-knlm"):
        model_type = "cong" if "cong" in tokenizer_name.lower() else "knlm"
        def _kiwi_morphs(text, _model_type=model_type):
            from kiwipiepy import Kiwi
            if _model_type not in _KIWI_INSTANCES:
                _KIWI_INSTANCES[_model_type] = Kiwi(model_type=_model_type)
            kiwi = _KIWI_INSTANCES[_model_type]
            return [t.form for t in kiwi.tokenize(text) if t.tag in KIWI_CONTENT_POS]
        return None, _kiwi_morphs
    elif tokenizer_name.lower() == "kkma":
        try:
            from konlpy.tag import Kkma
            analyzer = Kkma()
            return analyzer, analyzer.morphs
        except ImportError:
            raise RuntimeError("Kkma(konlpy)가 설치되어 있지 않습니다.")
    elif tokenizer_name.lower() == "whitespace":
        return None, lambda text: text.split()
    else:
        raise ValueError(
            f"Unsupported tokenizer: '{tokenizer_name}'. "
            "Choose one of: 'Okt', 'Mecab', 'Kkma', 'kiwi-cong', 'kiwi-knlm', 'whitespace'."
        )


# Cell 136
def compute_idf_dict(corpus: List[str], tokenizer: Callable[[str], List[str]]):
    """
    코퍼스 전체에 대해 IDF 딕셔너리, 평균 문서 길이, 어휘 크기를 계산한다.
    """
    doc_freq = defaultdict(int)
    total_docs = len(corpus)
    total_length = 0
    vocab_set = set()

    for doc in corpus:
        tokens = tokenizer(doc)
        total_length += len(tokens)
        unique_tokens = set(tokens)
        vocab_set.update(unique_tokens)
        for token in unique_tokens:
            doc_freq[token] += 1

    avgdl = total_length / total_docs

    idf_dict = {}
    for token, df in doc_freq.items():
        idf = math.log((total_docs - df + 0.5) / (df + 0.5) + 1)
        idf_dict[token] = idf

    vocab_size = len(vocab_set)
    return idf_dict, avgdl, vocab_size


# Cell 137
class BM25Embedder:
    """
    BM25 기반 스파스 임베딩 생성기.
    fit() 으로 코퍼스를 학습하고, embed_document() / embed_query() 로 벡터를 생성한다.
    결과는 {token_id: score} 딕셔너리 형태.
    """

    def __init__(self, k: float = 1.2, b: float = 0.75, tokenizer: str = "Mecab"):
        self.k = k
        self.b = b
        self.idf_dict: Dict[str, float] = {}
        self.avgdl: float = 0.0
        self.vocab_size: int = 0
        self.token_to_index: Dict[str, int] = {}
        self.index_to_token: Dict[int, str] = {}

        analyzer, morphs = _build_tokenizer(tokenizer)
        self.analyzer = analyzer
        self.tokenizer = morphs
        self._tokenizer = morphs

    def fit(self, corpus: List[str]):
        """코퍼스를 학습하여 IDF, 어휘, avgdl 을 초기화한다."""
        doc_freq = defaultdict(int)
        total_docs = len(corpus)
        total_length = 0
        vocab_set = set()

        for doc in corpus:
            tokens = self.tokenizer(doc)
            total_length += len(tokens)
            unique_tokens = set(tokens)
            vocab_set.update(unique_tokens)
            for token in unique_tokens:
                doc_freq[token] += 1

        self.avgdl = total_length / total_docs

        sorted_tokens = sorted(vocab_set)
        self.token_to_index = {token: idx for idx, token in enumerate(sorted_tokens)}
        self.index_to_token = {idx: token for token, idx in self.token_to_index.items()}
        self.vocab_size = len(self.token_to_index)

        for token, df in doc_freq.items():
            idf = math.log((total_docs - df + 0.5) / (df + 0.5) + 1)
            self.idf_dict[token] = idf

    def _term_freq(self, tokens: List[str]) -> Dict[str, int]:
        freq: Dict[str, int] = defaultdict(int)
        for token in tokens:
            freq[token] += 1
        return freq

    def _bm25_tf(self, f: int, doc_len: int) -> float:
        return (f * (self.k + 1)) / (f + self.k * (1 - self.b + self.b * doc_len / self.avgdl))

    def embed_document(self, doc: str) -> Dict[int, float]:
        """문서를 BM25 스파스 벡터 딕셔너리로 변환한다."""
        tokens = self.tokenizer(doc)
        doc_len = len(tokens)
        tf_raw = self._term_freq(tokens)

        sparse_vec: Dict[int, float] = {}
        for token, f in tf_raw.items():
            if token not in self.token_to_index:
                continue
            token_id = self.token_to_index[token]
            tf_score = self._bm25_tf(f, doc_len)
            idf = self.idf_dict.get(token, 0.0)
            sparse_vec[token_id] = tf_score * idf
        return sparse_vec

    def embed_query(self, query: str) -> Dict[int, float]:
        """쿼리를 BM25 스파스 벡터 딕셔너리로 변환한다 (TF=1)."""
        tokens = set(self.tokenizer(query))
        sparse_vec: Dict[int, float] = {}
        for token in tokens:
            if token not in self.token_to_index:
                continue
            token_id = self.token_to_index[token]
            sparse_vec[token_id] = 1.0
        return sparse_vec


class BM25Embedder_PG(BM25Embedder):
    """
    pgvector SparseVector 를 직접 반환하는 BM25Embedder 서브클래스.
    pgvector 가 설치되지 않은 경우 dict를 반환한다.
    """

    def embed_document(self, doc: str):
        sparse_dict = super().embed_document(doc)
        if SparseVector is not None:
            return SparseVector(sparse_dict, self.vocab_size)
        return sparse_dict

    def embed_query(self, query: str):
        sparse_dict = super().embed_query(query)
        if SparseVector is not None:
            return SparseVector(sparse_dict, self.vocab_size)
        return sparse_dict

    def save_vocab(self, path: str) -> None:
        """Persist vocabulary + IDF stats to a JSON file so fresh sessions can
        restore the exact same token→index mapping used when the DB table was built."""
        import os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        payload = {
            "token_to_index": self.token_to_index,
            "idf_dict": self.idf_dict,
            "avgdl": self.avgdl,
            "vocab_size": self.vocab_size,
            "k": self.k,
            "b": self.b,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    def load_vocab(self, path: str) -> bool:
        """Restore vocabulary + IDF stats from a previously saved JSON file.
        Returns True on success, False if file does not exist."""
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        self.token_to_index = payload["token_to_index"]
        self.index_to_token = {int(v): k for k, v in self.token_to_index.items()}
        self.idf_dict = payload["idf_dict"]
        self.avgdl = payload["avgdl"]
        self.vocab_size = payload["vocab_size"]
        self.k = payload["k"]
        self.b = payload["b"]
        return True


# --- 6. 코퍼스 로딩 및 학습 ---

# Cell 75
SAMPLE_SENTENCES: List[str] = [
    "미세 플라스틱을 포함하는 수질 샘플로부터 0.01마이크론 단위의 입자를 정밀하게 측정할 수 있도록 구성되며, 제주대학교 화학과에서 개발된 분석기 N-3000을 이용하여 미세 입자의 정량 분석 정확도를 향상시키는 미세 플라스틱 분석 시스템.",
    "지하철역 승강장 출입구를 기준으로 특정 시간대의 유입 인구를 실시간으로 계측하고, 해당 시간대 평균 유동 인구가 5,000명을 초과할 경우 경고 데이터를 생성하는 기능을 포함하며, 교통 밀집 구간 분석에 활용되는 도시 교통 유입량 예측 시스템.",
    "비타민 C 함량이 기존 사과 품종 대비 40% 이상 향상되도록 설계된 유전자형을 포함하며, 충북농업기술원에 의해 개발된 신품종으로서 건강 기능성이 강화된 고영양 과일 품종.",
    "자율 주행 탐사 로봇에 설치된 채굴 장치를 통해 행성 표면에서 황 화합물을 자동 채취하고, 게일 분화구와 같은 특정 지형에서 평균 1kg의 시료를 안정적으로 수집하도록 구성된 화성 탐사용 지질 채집 시스템.",
    "도서 이용 이력 및 선호 장르 데이터를 기반으로 개인화된 추천 콘텐츠를 제공하며, ML-RecoEngine 알고리즘을 도입하여 시범적으로 운영되는 맞춤형 자료 추천 시스템.",
    "야생 동물의 이동 경로를 AI 기반 드론이 실시간으로 추적하고, 고정밀 센서를 이용하여 98% 이상의 데이터 정확도로 위치 및 방향성을 기록하도록 구성된 생태계 모니터링 시스템.",
    "항만 내 화물 처리 작업 흐름을 블록체인 기반으로 자동 관리하고, 평균 작업 시간을 6시간에서 3시간으로 단축하며, 독일 함부르크항에 적용된 고효율 스마트 물류 시스템.",
    "AI 기반 고객 행동 분석 알고리즘을 이용하여 백화점 내 구매 패턴을 예측하고, 신제품 출시 후 첫 주간 특정 제품군의 매출을 150% 상승시키는 효과를 가지는 리테일 최적화 시스템.",
    "공연장 내 음향 환경을 자동 분석하고, Yamaha RAVAGE PM10 장비를 통해 실시간으로 음향 특성을 조정함으로써, 관객 만족도를 95% 이상으로 유지하는 스마트 음향 제어 시스템.",
    "해양 생태계 감시를 위해 설치된 센서 네트워크를 통해 연간 약 3만 건의 바닷새 행동 데이터를 수집하고, 수집된 데이터를 기반으로 계절별 행동 변화를 분석할 수 있는 생물군 행동 추적 시스템.",
    "개발도상국 내 친환경 에너지 인프라 구축을 목적으로, 2025년까지 총 50개 국가에 태양광 발전소를 설치하며, 에너지 자립률 향상을 지원하는 분산형 신재생 발전 시스템.",
    "신생 스타트업의 기술 역량 분석 및 맞춤형 투자 연결을 자동화하며, 200개 기업을 분석하여 60%의 투자 유치 성공률을 기록한 인공지능 기반 스타트업 지원 플랫폼.",
    "암세포 조직을 고해상도로 탐지할 수 있도록 구성된 X-Tracer 장비를 포함하며, 기존 대비 20% 향상된 진단 정확도를 제공하는 정밀 병리 진단 장치.",
    "국제적 AI 기술 협력을 위한 다국적 분산 플랫폼을 기반으로, 50개국 1,000명 이상의 연구자가 동시에 실시간 접속하여 지능형 알고리즘을 실증하는 글로벌 AI 테스트베드.",
    "피부암 세포의 초기 신호를 비침습적으로 감지할 수 있는 광 스캐닝 장치를 포함하며, 기존 85% 수준의 진단 정확도를 92%로 향상시키는 조기 진단 장비.",
    "IoT 센서를 활용하여 실시간으로 공장 내 에너지 소비를 모니터링하고, 시스템 최적화를 통해 전력 소비량을 30% 절감하는 스마트 공장 에너지 관리 기술.",
    "3차원 적층 제조를 위한 초고속 3D 프린터로서, 자동차 부품 생산 공정의 총 소요 시간을 기존의 절반 수준으로 단축하는 고속 출력 기반 제조 장치.",
    "생명과학 분야의 기술 자료와 투자 수요를 연결하는 빅데이터 기반 플랫폼을 통해 총 20억 달러 규모의 글로벌 투자 유치를 실현한 바이오 비즈니스 매칭 시스템.",
    "항만 운영 효율화를 위해 도입된 신형 크레인이 컨테이너 적재 작업을 자동화하고, 시간당 300개의 컨테이너를 처리할 수 있도록 설계된 스마트 하역 시스템.",
    "AI 기반 기술 자문 알고리즘을 통해 스타트업의 기술 현황을 정밀 분석하고, 매달 10개 이상의 기업에 맞춤형 솔루션을 제공하는 자동화 기술 컨설팅 시스템.",
]


def load_corpus_from_db(table: str = "text_embedding") -> List[str]:
    """
    DB의 text_embedding 테이블에서 text 컬럼 전체를 읽어 반환한다.
    DB에 접근할 수 없거나 행이 없으면 SAMPLE_SENTENCES 를 반환한다.
    """
    rows = execute_query(f"SELECT text FROM {table} ORDER BY id;")
    if rows:
        return [row[0] for row in rows]
    print(f"[load_corpus_from_db] {table} 에서 데이터를 읽지 못함. SAMPLE_SENTENCES 사용.")
    return SAMPLE_SENTENCES


def fit_bm25_from_corpus(
    corpus: List[str],
    tokenizer: str = "Mecab",
    k: float = 1.2,
    b: float = 0.75,
) -> BM25Embedder_PG:
    """
    코퍼스로 BM25Embedder_PG 를 학습하고 반환한다.
    Cell 139 참조.
    """
    bm25 = BM25Embedder_PG(k=k, b=b, tokenizer=tokenizer)
    bm25.fit(corpus)
    print(f"[fit_bm25_from_corpus] vocab_size={bm25.vocab_size}, avgdl={bm25.avgdl:.2f}")
    return bm25


# --- 7. 검색 함수 ---

# Cell 145
def bm25_sparse_search(
    bm25: BM25Embedder_PG,
    query_text: str,
    k: int = 5,
    table: str = "text_embedding_sparse_bm25",
) -> List[tuple]:
    """
    pgvector sparsevec 기반 BM25 유사도 검색.
    낮은 cosine distance = 더 유사.
    """
    query_vec = bm25.embed_query(query_text)
    result = execute_query(
        f"""
        SELECT id, text, emb_sparse <=> %s AS bm25_score
        FROM {table}
        ORDER BY bm25_score
        LIMIT %s;
        """,
        (query_vec, k),
    )
    return result or []


# Cell 129 / Cell 130 — PostgreSQL 내장 bm25_ranking() 함수 기반 검색
def bm25_sql_search(query_text: str, k: int = 5) -> List[tuple]:
    """
    PostgreSQL의 bm25_ranking() 함수를 사용해 text_embedding 테이블을 검색한다.
    textsearch_ko(mecab-ko) 확장이 설치되어 있어야 한다.
    """
    result = execute_query(
        """
        SELECT e.id, e.text, b.score
        FROM bm25_ranking(%s) AS b
        JOIN text_embedding e ON e.id = b.doc_id
        ORDER BY b.score DESC
        LIMIT %s;
        """,
        (query_text, k),
    )
    return result or []


# Cell 37
def cosine_search(
    query_vector: Any,
    k: int = 5,
    table: str = "text_embedding",
) -> List[tuple]:
    """
    pgvector 코사인 거리 기반 벡터 유사도 검색.
    query_vector: numpy 배열 또는 리스트.
    """
    result = execute_query(
        f"""
        SELECT id, text, emb <=> %s::vector AS cosine_distance
        FROM {table}
        ORDER BY cosine_distance
        LIMIT %s;
        """,
        (query_vector, k),
    )
    return result or []


# Cell 162
def hybrid_search_linear(
    query_text: str,
    query_vector: Any,
    k: int = 10,
    bm25_weight: float = 0.5,
    vector_weight: float = 0.5,
) -> List[tuple]:
    """
    Linear Combination 앙상블 검색.
    BM25 점수와 코사인 유사도(1 - distance)를 가중합산한다.
    textsearch_ko 확장 및 bm25_ranking() 함수가 필요하다.
    """
    sql = """
        WITH bm25_results AS (
            SELECT doc_id, score AS bm25_score
            FROM bm25_ranking(%s)
        ),
        vector_results AS (
            SELECT
                id AS doc_id,
                text,
                emb <=> %s::vector AS cosine_distance
            FROM text_embedding
            ORDER BY cosine_distance
            LIMIT %s
        )
        SELECT
            vr.doc_id,
            vr.text,
            (COALESCE(bm25_score, 0) * %s + (1 - vr.cosine_distance) * %s) AS final_score
        FROM vector_results vr
        LEFT JOIN bm25_results br ON vr.doc_id = br.doc_id
        ORDER BY final_score DESC;
    """
    result = execute_query(sql, (query_text, query_vector, k, bm25_weight, vector_weight))
    return result or []


# Cell 163
def hybrid_search_rrf(
    query_text: str,
    query_vector: Any,
    k: int = 10,
    rrf_constant: int = 60,
    fallback_rank: int = 1000,
) -> List[tuple]:
    """
    Reciprocal Rank Fusion(RRF) 앙상블 검색.
    BM25 랭킹과 벡터 유사도 랭킹을 RRF 공식으로 결합한다.
    textsearch_ko 확장 및 bm25_ranking() 함수가 필요하다.
    """
    sql = f"""
        WITH bm25_results AS (
            SELECT doc_id, score AS bm25_score,
                   RANK() OVER (ORDER BY score DESC) AS bm25_rank
            FROM bm25_ranking(%s)
        ),
        vector_results AS (
            SELECT
                id AS doc_id,
                text,
                emb <=> %s::vector AS cosine_distance,
                RANK() OVER (ORDER BY emb <=> %s::vector ASC) AS vector_rank
            FROM text_embedding
            LIMIT %s
        )
        SELECT
            vr.doc_id,
            vr.text,
            (1.0 / ({rrf_constant} + COALESCE(bm25_rank, {fallback_rank})))
            + (1.0 / ({rrf_constant} + vector_rank)) AS final_score
        FROM vector_results vr
        LEFT JOIN bm25_results br ON vr.doc_id = br.doc_id
        ORDER BY final_score DESC;
    """
    result = execute_query(sql, (query_text, query_vector, query_vector, k))
    return result or []


# --- 8. 메인 실행 예제 ---

def setup_sparse_bm25_table(
    bm25: BM25Embedder_PG,
    corpus: List[str],
    table: str = "text_embedding_sparse_bm25",
):
    """
    BM25 스파스 벡터 테이블을 생성하고 문서를 저장한다.
    table 파라미터로 토크나이저별 별도 테이블 사용 가능
    (예: text_embedding_sparse_bm25_mecab, _kkma 등).
    """
    vocab_size = bm25.vocab_size
    execute_query(f"DROP TABLE IF EXISTS {table};")
    execute_query(f"""
        CREATE TABLE {table} (
            id SERIAL PRIMARY KEY,
            text TEXT NOT NULL,
            emb_sparse sparsevec({vocab_size})
        );
    """)
    for doc in corpus:
        emb = bm25.embed_document(doc)
        execute_query(
            f"INSERT INTO {table} (text, emb_sparse) VALUES (%s, %s)",
            (doc, emb),
        )
    print(f"[setup_sparse_bm25_table] {len(corpus)}개 문서 저장 완료 (table={table}, vocab_size={vocab_size}).")


def test_tokenizers():
    """Smoke test for whitespace and kiwi tokenizers."""
    # Whitespace (no external deps)
    ws_tok = _build_tokenizer("whitespace")[1]
    result = ws_tok("hello world foo")
    assert result == ["hello", "world", "foo"], f"whitespace failed: {result}"
    print(f"[test_tokenizers] whitespace: {result}")

    # kiwi-cong (requires kiwipiepy)
    try:
        kiwi_tok = _build_tokenizer("kiwi-cong")[1]
        result = kiwi_tok("한국어 형태소 분석 테스트")
        assert isinstance(result, list) and len(result) > 0, f"kiwi-cong returned empty: {result}"
        print(f"[test_tokenizers] kiwi-cong: {result}")
    except ImportError:
        print("[test_tokenizers] kiwi-cong: kiwipiepy not installed, skipping.")

    print("[test_tokenizers] done.")


if __name__ == "__main__":
    # TODO: OpenAI API 키를 환경변수 OPENAI_API_KEY 에 설정하세요.
    #       dense 검색(get_embedding)을 사용하려면 openai 패키지가 필요합니다.
    # TODO: PostgreSQL에 textsearch_ko(mecab-ko) 확장이 설치되어 있어야
    #       bm25_sql_search(), hybrid_search_linear(), hybrid_search_rrf() 가 동작합니다.

    print("=== BM25 모듈 실행 예제 ===\n")

    # 1. 코퍼스 설정 (DB 또는 샘플)
    corpus = SAMPLE_SENTENCES
    print(f"코퍼스 문장 수: {len(corpus)}\n")

    # 2. BM25 스파스 임베더 학습
    # Cell 139
    bm25 = fit_bm25_from_corpus(corpus, tokenizer="Mecab")
    print(f"vocab_size = {bm25.vocab_size}\n")

    # 3. 스파스 BM25 테이블 구성
    # Cell 141, 143
    setup_sparse_bm25_table(bm25, corpus)

    # 4. 스파스 BM25 검색
    # Cell 144, 145
    query_text = "AI 기술"
    print(f"--- BM25 스파스 검색: '{query_text}' ---")
    query_vec = bm25.embed_query(query_text)

    # 쿼리 벡터 구성 확인 (Cell 144)
    if SparseVector is not None and hasattr(query_vec, '_indices') and hasattr(query_vec, '_values'):
        indices = query_vec._indices  # type: ignore[union-attr]
        values = query_vec._values  # type: ignore[union-attr]
        for idx, val in zip(indices, values):
            token = bm25.index_to_token.get(idx, "<unknown>")
            print(f"  {idx}: {token} (value: {val})")
    print()

    results = bm25_sparse_search(bm25, query_text, k=5)
    for row in results:
        print(row)
    print()

    # 5. SQL bm25_ranking() 기반 검색 (textsearch_ko 필요)
    # Cell 129, 130
    print(f"--- SQL bm25_ranking() 검색: '{query_text}' ---")
    sql_results = bm25_sql_search(query_text, k=5)
    for row in sql_results:
        print(row)
    print()

    # 6. 앙상블 검색 (dense 벡터 필요)
    # TODO: query_vector 는 OpenAI 또는 다른 임베딩 모델로 생성해야 합니다.
    #       아래는 실행 구조만 보여주는 placeholder 예시입니다.
    # query_vector = get_embedding([query_text])[0]  # openai 사용 시

    # Linear Combination 예시 (Cell 162)
    # print(f"--- Linear Combination 앙상블: '{query_text}' ---")
    # linear_results = hybrid_search_linear(query_text, query_vector, k=10)
    # for row in linear_results:
    #     print(row)

    # RRF 예시 (Cell 163)
    # print(f"--- RRF 앙상블: '{query_text}' ---")
    # rrf_results = hybrid_search_rrf(query_text, query_vector, k=10)
    # for row in rrf_results:
    #     print(row)

    print("=== 완료 ===")
