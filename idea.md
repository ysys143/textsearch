
0. 데이터 준비

모든 데이터는 하나의 청크가 1~10K 정도의 장문. 질의는 실제 채팅에서 쓰일 법한 복잡한 질의 문장(단순 키워드 아님). 적절한 데이터 

1. tsvector에 한국어 형태소 분석기를 넣을 수 있는 최선의 방법.

textsearch_ko vs custom


2. pg에서 한국어 bm25를 구현하는 최선의 방법 
pg_textsearch vs pg_bm25 vs pl/pgsql custom

pg_textsearch + 한국어 형태소 분석기 접합 가능한가


3. 최고의 한국어 형태소 분석기

Kiwi CoNg, mecab, okt, kkma, khaiii, space


4. 형태소분석기 + bm25 vs neural sparse search

neural sparse search
- yjoonjang/splade-ko-v1 - BAAI/BGE-M3 sparse https://github.com/OnAnd0n/ko-embedding-leaderboard 


5. Postgres(1~4에서 발견된 최선의 세팅) vs Elastic, Vespa, Qdrant BM25/Hybrid Search. 

데이터 스케일링에 따른 latency & recall 비교.

