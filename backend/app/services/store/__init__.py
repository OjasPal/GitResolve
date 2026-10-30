from app.core.config import Settings
from app.services.store.base import Store


def build_store(settings: Settings) -> Store:
    if settings.store_backend == "firestore":
        from app.services.store.firestore import FirestoreStore

        return FirestoreStore(settings.gcp_project, settings.firestore_database)
    from app.services.store.memory import MemoryStore

    return MemoryStore()
