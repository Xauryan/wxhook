#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdint.h>
#include <string.h>

#include "sqlite3ext.h"
SQLITE_EXTENSION_INIT1

#define WEIXIN_CREATE_RVA 0x00F4A110u
#define WEIXIN_DELETE_RVA 0x00F4A0F0u
#define WEIXIN_TOKENIZE_RVA 0x00F49F90u
#define WEIXIN_TIMESTAMP 0x69E5E919u
#define WEIXIN_IMAGE_SIZE 0x0ABEE000u

#define ARRAY_COUNT(value) (sizeof(value) / sizeof((value)[0]))

typedef int (*WeixinCreateFn)(
    void *, const char **, int, Fts5Tokenizer **
);
typedef intptr_t (*WeixinDeleteFn)(Fts5Tokenizer *);
typedef int (*WeixinTokenizeFn)(
    Fts5Tokenizer *,
    void *,
    int,
    const char *,
    int,
    int (*)(void *, int, const char *, int, int, int)
);

static HMODULE g_weixin_module;
static SRWLOCK g_weixin_lock = SRWLOCK_INIT;
static WeixinCreateFn g_weixin_create;
static WeixinDeleteFn g_weixin_delete;
static WeixinTokenizeFn g_weixin_tokenize;

static const unsigned char kCreateSignature[] = {
    0x55, 0x41, 0x56, 0x56, 0x57, 0x53, 0x48, 0x83,
    0xEC, 0x30, 0x48, 0x8D, 0x6C, 0x24, 0x30, 0x48,
};
static const unsigned char kDeleteSignature[] = {
    0x48, 0x83, 0xEC, 0x28, 0x48, 0x85, 0xC9, 0x74,
    0x0B, 0x48, 0x8B, 0x01, 0xBA, 0x01, 0x00, 0x00,
};
static const unsigned char kTokenizeSignature[] = {
    0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54,
    0x56, 0x57, 0x55, 0x53, 0x48, 0x83, 0xEC, 0x58,
};

static int weixin_create(
    void *context,
    const char **arguments,
    int argument_count,
    Fts5Tokenizer **tokenizer
) {
    return g_weixin_create(context, arguments, argument_count, tokenizer);
}

static void weixin_delete(Fts5Tokenizer *tokenizer) {
    (void)g_weixin_delete(tokenizer);
}

static int weixin_tokenize(
    Fts5Tokenizer *tokenizer,
    void *context,
    int flags,
    const char *text,
    int text_size,
    int (*token_callback)(void *, int, const char *, int, int, int)
) {
    return g_weixin_tokenize(
        tokenizer,
        context,
        flags,
        text,
        text_size,
        token_callback
    );
}

static fts5_api *fts5_api_from_db(sqlite3 *database) {
    fts5_api *api = NULL;
    sqlite3_stmt *statement = NULL;
    int result = sqlite3_prepare_v2(
        database,
        "SELECT fts5(?1)",
        -1,
        &statement,
        NULL
    );
    if (result == SQLITE_OK) {
        result = sqlite3_bind_pointer(
            statement,
            1,
            (void *)&api,
            "fts5_api_ptr",
            NULL
        );
    }
    if (result == SQLITE_OK) {
        (void)sqlite3_step(statement);
    }
    (void)sqlite3_finalize(statement);
    return api;
}

static int validate_pe_image(HMODULE module) {
    const unsigned char *base = (const unsigned char *)module;
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)base;
    const IMAGE_NT_HEADERS64 *nt;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) {
        return 0;
    }
    nt = (const IMAGE_NT_HEADERS64 *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE
        || nt->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64
        || nt->FileHeader.TimeDateStamp != WEIXIN_TIMESTAMP
        || nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC
        || nt->OptionalHeader.SizeOfImage != WEIXIN_IMAGE_SIZE
        || WEIXIN_CREATE_RVA + sizeof(kCreateSignature) > WEIXIN_IMAGE_SIZE
        || WEIXIN_DELETE_RVA + sizeof(kDeleteSignature) > WEIXIN_IMAGE_SIZE
        || WEIXIN_TOKENIZE_RVA + sizeof(kTokenizeSignature) > WEIXIN_IMAGE_SIZE) {
        return 0;
    }
    if (memcmp(base + WEIXIN_CREATE_RVA, kCreateSignature, sizeof(kCreateSignature)) != 0
        || memcmp(base + WEIXIN_DELETE_RVA, kDeleteSignature, sizeof(kDeleteSignature)) != 0
        || memcmp(base + WEIXIN_TOKENIZE_RVA, kTokenizeSignature, sizeof(kTokenizeSignature)) != 0) {
        return 0;
    }
    return 1;
}

static int load_weixin_callbacks(char **error_message) {
    wchar_t path[32768];
    DWORD path_size;
    HMODULE module;
    int result = SQLITE_OK;
    AcquireSRWLockExclusive(&g_weixin_lock);
    if (g_weixin_module != NULL) {
        ReleaseSRWLockExclusive(&g_weixin_lock);
        return SQLITE_OK;
    }

    path_size = GetEnvironmentVariableW(
        L"WEIXIN_DLL_PATH",
        path,
        (DWORD)ARRAY_COUNT(path)
    );
    if (path_size == 0) {
        static const wchar_t default_path[] =
            L"D:\\Program Files\\Tencent\\Weixin\\4.1.9.23\\Weixin.dll";
        memcpy(path, default_path, sizeof(default_path));
    } else if (path_size >= ARRAY_COUNT(path)) {
        *error_message = sqlite3_mprintf("WEIXIN_DLL_PATH is too long");
        result = SQLITE_ERROR;
        goto finish;
    }

    module = LoadLibraryExW(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (module == NULL) {
        *error_message = sqlite3_mprintf(
            "failed to load Weixin.dll, Win32 error %lu",
            (unsigned long)GetLastError()
        );
        result = SQLITE_ERROR;
        goto finish;
    }
    if (!validate_pe_image(module)) {
        FreeLibrary(module);
        *error_message = sqlite3_mprintf(
            "unsupported Weixin.dll; expected version 4.1.9.23"
        );
        result = SQLITE_ERROR;
        goto finish;
    }

    g_weixin_create = (WeixinCreateFn)(
        (unsigned char *)module + WEIXIN_CREATE_RVA
    );
    g_weixin_delete = (WeixinDeleteFn)(
        (unsigned char *)module + WEIXIN_DELETE_RVA
    );
    g_weixin_tokenize = (WeixinTokenizeFn)(
        (unsigned char *)module + WEIXIN_TOKENIZE_RVA
    );
    g_weixin_module = module;
    (void)GetModuleHandleExW(
        GET_MODULE_HANDLE_EX_FLAG_PIN | GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
        (LPCWSTR)module,
        &module
    );

finish:
    ReleaseSRWLockExclusive(&g_weixin_lock);
    return result;
}

__declspec(dllexport) int sqlite3_extension_init(
    sqlite3 *database,
    char **error_message,
    const sqlite3_api_routines *api_routines
) {
    fts5_api *fts_api;
    fts5_tokenizer tokenizer;
    fts5_tokenizer existing_tokenizer;
    void *existing_context = NULL;
    int result;

    SQLITE_EXTENSION_INIT2(api_routines);
    fts_api = fts5_api_from_db(database);
    if (fts_api == NULL || fts_api->iVersion < 2) {
        *error_message = sqlite3_mprintf("SQLite FTS5 API is unavailable");
        return SQLITE_ERROR;
    }
    result = fts_api->xFindTokenizer(
        fts_api,
        "MMFtsTokenizer",
        &existing_context,
        &existing_tokenizer
    );
    if (result == SQLITE_OK) {
        if (existing_tokenizer.xCreate == weixin_create
            && existing_tokenizer.xDelete == weixin_delete
            && existing_tokenizer.xTokenize == weixin_tokenize) {
            return SQLITE_OK;
        }
        *error_message = sqlite3_mprintf(
            "a conflicting MMFtsTokenizer is already registered"
        );
        return SQLITE_ERROR;
    }
    result = load_weixin_callbacks(error_message);
    if (result != SQLITE_OK) {
        return result;
    }

    tokenizer.xCreate = weixin_create;
    tokenizer.xDelete = weixin_delete;
    tokenizer.xTokenize = weixin_tokenize;
    result = fts_api->xCreateTokenizer(
        fts_api,
        "MMFtsTokenizer",
        NULL,
        &tokenizer,
        NULL
    );
    if (result != SQLITE_OK) {
        *error_message = sqlite3_mprintf(
            "failed to register MMFtsTokenizer: SQLite error %d",
            result
        );
    }
    return result;
}
