from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    query: str = Field(min_length=2, max_length=400)
    top_k: int | None = Field(default=None, ge=1, le=50)
    include_files: bool = True
    thread_id: int | None = Field(default=None, ge=1)
    file_ids: list[int] | None = None
    search_mode: str = Field(default="auto")


class Citation(BaseModel):
    title: str
    url: str
    snippet: str


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    chat_id: int
    thread_id: int
    confidence: float = 0.0


class UploadResponse(BaseModel):
    file_id: int
    filename: str
    mime_type: str
    size_bytes: int
    extracted_text_chars: int
    job_id: int | None = None


class ChatItem(BaseModel):
    id: int
    thread_id: int
    created_at: str
    query: str
    answer: str
    title: str | None = None


class ChatDetail(BaseModel):
    id: int
    thread_id: int
    created_at: str
    query: str
    answer: str
    citations: list[Citation]


class FileItem(BaseModel):
    id: int
    created_at: str
    original_name: str
    mime_type: str
    size_bytes: int
    extracted_text_chars: int


class ThreadDetail(BaseModel):
    thread_id: int
    chats: list[ChatDetail]
    attached_file_ids: list[int]


class ThreadFilesRequest(BaseModel):
    file_ids: list[int] = []


class ThreadFilesResponse(BaseModel):
    thread_id: int
    file_ids: list[int]


class LoginRequest(BaseModel):
    password: str = ""


class LoginResponse(BaseModel):
    ok: bool
    auth_enabled: bool


class ReindexRequest(BaseModel):
    file_ids: list[int] | None = None
    limit: int | None = Field(default=None, ge=1, le=5000)


class ReindexResponse(BaseModel):
    requested: int
    indexed: int
    failed: list[dict]


class PurgeRequest(BaseModel):
    confirm: bool = False


class PurgeResponse(BaseModel):
    deleted_chat_count: int
    deleted_file_count: int
    deleted_chunk_count: int
    deleted_upload_count: int


class JobItem(BaseModel):
    id: int
    created_at: str
    updated_at: str
    job_type: str
    status: str
    progress: float
    total: int
    completed: int
    message: str
    payload_json: str


class ReindexStartResponse(BaseModel):
    job_id: int
    status: str


class FollowupsResponse(BaseModel):
    chat_id: int
    suggestions: list[str]


class BackupItem(BaseModel):
    name: str
    created_at: str
    size_bytes: int


class BackupRestoreRequest(BaseModel):
    confirm: bool = False


class BackupRestoreResponse(BaseModel):
    restored_from: str
    pre_restore_backup: str


class ThreadTitleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ThreadTitleResponse(BaseModel):
    thread_id: int
    title: str
