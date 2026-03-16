import os
import shutil

# 先尽量关闭 Chroma 遥测噪音
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from utils.config_handler import chroma_conf
from model.factory import embed_model
from utils.path_tool import get_abs_path
from utils.file_handler import (
    txt_loader,
    pdf_loader,
    listdir_with_allowed_type,
    get_file_md5_hex,
)
from utils.logger_handler import logger


class VectorStoreService:
    def __init__(self, reset_db: bool = False):
        self.persist_directory = get_abs_path(chroma_conf["persist_directory"])
        self.md5_store_path = get_abs_path(chroma_conf["md5_hex_store"])

        # 如果旧的 Chroma 持久化数据和当前版本不兼容，直接重建
        if reset_db and os.path.exists(self.persist_directory):
            logger.warning(f"[向量库] 检测到 reset_db=True，删除旧持久化目录: {self.persist_directory}")
            shutil.rmtree(self.persist_directory, ignore_errors=True)

        # 确保持久化目录存在
        os.makedirs(self.persist_directory, exist_ok=True)

        self.vector_store = Chroma(
            collection_name=chroma_conf["collection_name"],
            embedding_function=embed_model,
            persist_directory=self.persist_directory,
        )

        self.spliter = RecursiveCharacterTextSplitter(
            chunk_size=chroma_conf["chunk_size"],
            chunk_overlap=chroma_conf["chunk_overlap"],
            separators=chroma_conf["separator"],
            length_function=len,
        )

    def get_retriever(self):
        return self.vector_store.as_retriever(
            search_kwargs={"k": chroma_conf["k"]}
        )

    def _check_md5_hex(self, md5_for_check: str) -> bool:
        md5_dir = os.path.dirname(self.md5_store_path)
        if md5_dir:
            os.makedirs(md5_dir, exist_ok=True)

        if not os.path.exists(self.md5_store_path):
            with open(self.md5_store_path, "w", encoding="utf-8"):
                pass
            return False

        with open(self.md5_store_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip() == md5_for_check:
                    return True
        return False

    def _save_md5_hex(self, md5_for_check: str):
        md5_dir = os.path.dirname(self.md5_store_path)
        if md5_dir:
            os.makedirs(md5_dir, exist_ok=True)

        with open(self.md5_store_path, "a", encoding="utf-8") as f:
            f.write(md5_for_check + "\n")

    @staticmethod
    def _get_file_documents(read_path: str) -> list[Document]:
        lower_path = read_path.lower()

        if lower_path.endswith(".txt"):
            return txt_loader(read_path)

        if lower_path.endswith(".pdf"):
            return pdf_loader(read_path)

        return []

    def load_document(self):
        """
        从数据文件夹内读取数据文件, 转为向量存入向量库
        同时计算文件的 MD5 做去重
        """
        allowed_files_path: list[str] = listdir_with_allowed_type(
            get_abs_path(chroma_conf["data_path"]),
            tuple(chroma_conf["allow_knowledge_file_type"]),
        )

        if not allowed_files_path:
            logger.warning("[加载知识库] 数据目录下没有可用文件")
            return

        for path in allowed_files_path:
            md5_hex = get_file_md5_hex(path)

            if self._check_md5_hex(md5_hex):
                logger.info(f"[加载知识库] {path} 内容已存在知识库内，跳过")
                continue

            try:
                documents: list[Document] = self._get_file_documents(path)

                if not documents:
                    logger.warning(f"[加载知识库] {path} 内没有有效文本内容，跳过")
                    continue

                split_document: list[Document] = self.spliter.split_documents(documents)

                if not split_document:
                    logger.warning(f"[加载知识库] {path} 分片后没有有效文本内容，跳过")
                    continue

                self.vector_store.add_documents(split_document)
                self._save_md5_hex(md5_hex)

                logger.info(f"[加载知识库] {path} 内容加载成功，共 {len(split_document)} 个分片")

            except Exception as e:
                logger.error(f"[加载知识库] {path} 加载失败: {str(e)}", exc_info=True)
                continue


if __name__ == '__main__':
    # 第一次修库时，先设为 True，重建向量库
    vs = VectorStoreService(reset_db=True)

    vs.load_document()
    retriever = vs.get_retriever()

    res = retriever.invoke("迷路")
    for r in res:
        print(r.page_content)
        print("=" * 20)