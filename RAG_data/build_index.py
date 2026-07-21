"""知识发布时构建当前 RAG_BACKEND 对应的索引。"""
from agents.rag_backend import build_index


if __name__ == "__main__":
    count = build_index()
    print(f"已构建 {count} 个知识片段")
