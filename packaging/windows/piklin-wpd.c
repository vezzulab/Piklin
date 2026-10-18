/* piklin-wpd.exe: reads phones and cameras through Windows Portable Devices.
 *
 * On Linux a phone is a folder gvfs mounts, and on a Mac Image Capture
 * reaches it; on Windows an iPhone, an Android phone in "File transfer" mode or
 * a camera in PTP mode is only reachable through WPD (see piklin/wpd.py). This
 * program does the COM work, in a process of its own: a phone that stops
 * answering cannot take Piklin's window with it. It prints one JSON object per
 * line on stdout.
 * It only ever reads a device: there is no command that changes or deletes anything on it.
 *
 *   piklin-wpd list                       the devices now connected
 *   piklin-wpd scan <device>              every file on one, as it is found
 *   piklin-wpd thumbs <device>            stdin: "<object>\t<file>" per line;
 *                                         saves each one's small picture; a third column
 *                                         saves the whole photo too
 *   piklin-wpd fetch <device> <object> <file>     copies one file off it
 *
 * Build: gcc -O2 -municode -DCOBJMACROS piklin-wpd.c -lportabledeviceguids -lole32 -loleaut32
 */
#define INITGUID
#include <windows.h>
#include <objbase.h>
#include <propvarutil.h>
#include <portabledeviceapi.h>
#include <portabledevice.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#include <io.h>
#include <fcntl.h>

#define MAX_DEPTH 12
#define MAX_FILES 100000

static void put_json_string(const wchar_t *s)
{
    char *u;
    int n = WideCharToMultiByte(CP_UTF8, 0, s, -1, NULL, 0, NULL, NULL);
    if (n <= 0) { fputs("\"\"", stdout); return; }
    u = malloc(n);
    WideCharToMultiByte(CP_UTF8, 0, s, -1, u, n, NULL, NULL);
    fputc('"', stdout);
    for (const unsigned char *p = (const unsigned char *)u; *p; p++) {
        if (*p == '"' || *p == '\\') { fputc('\\', stdout); fputc(*p, stdout); }
        else if (*p < 0x20) fprintf(stdout, "\\u%04x", *p);
        else fputc(*p, stdout);
    }
    fputc('"', stdout);
    free(u);
}

static void error_line(const char *what, HRESULT hr)
{
    printf("{\"error\":\"%s\",\"hr\":\"0x%08lx\"}\n", what, (unsigned long)hr);
    fflush(stdout);
}

static IPortableDevice *open_device(const wchar_t *id, DWORD access)
{
    IPortableDeviceValues *info = NULL;
    IPortableDevice *dev = NULL;
    HRESULT hr = CoCreateInstance(&CLSID_PortableDeviceValues, NULL, CLSCTX_INPROC_SERVER,
                                  &IID_IPortableDeviceValues, (void **)&info);
    if (FAILED(hr)) { error_line("values", hr); return NULL; }
    IPortableDeviceValues_SetStringValue(info, &WPD_CLIENT_NAME, L"Piklin");
    IPortableDeviceValues_SetUnsignedIntegerValue(info, &WPD_CLIENT_MAJOR_VERSION, 2);
    IPortableDeviceValues_SetUnsignedIntegerValue(info, &WPD_CLIENT_MINOR_VERSION, 0);
    IPortableDeviceValues_SetUnsignedIntegerValue(info, &WPD_CLIENT_REVISION, 0);
    IPortableDeviceValues_SetUnsignedIntegerValue(info, &WPD_CLIENT_SECURITY_QUALITY_OF_SERVICE,
                                                  SECURITY_IMPERSONATION);
    IPortableDeviceValues_SetUnsignedIntegerValue(info, &WPD_CLIENT_DESIRED_ACCESS, access);
    hr = CoCreateInstance(&CLSID_PortableDeviceFTM, NULL, CLSCTX_INPROC_SERVER,
                          &IID_IPortableDevice, (void **)&dev);
    if (FAILED(hr)) { IPortableDeviceValues_Release(info); error_line("device", hr); return NULL; }
    hr = IPortableDevice_Open(dev, id, info);
    IPortableDeviceValues_Release(info);
    if (FAILED(hr)) {
        /* An iPhone that is locked, or has not been told to trust this computer. */
        error_line(hr == E_ACCESSDENIED ? "locked" : "open", hr);
        IPortableDevice_Release(dev);
        return NULL;
    }
    return dev;
}

/* -------------------------------------------------------------------- list */

static int cmd_list(void)
{
    IPortableDeviceManager *mgr = NULL;
    HRESULT hr = CoCreateInstance(&CLSID_PortableDeviceManager, NULL, CLSCTX_INPROC_SERVER,
                                  &IID_IPortableDeviceManager, (void **)&mgr);
    DWORD count = 0;
    if (FAILED(hr)) { error_line("manager", hr); return 1; }
    IPortableDeviceManager_RefreshDeviceList(mgr);
    if (FAILED(IPortableDeviceManager_GetDevices(mgr, NULL, &count)) || count == 0) {
        IPortableDeviceManager_Release(mgr);
        return 0;
    }
    PWSTR *ids = calloc(count, sizeof(PWSTR));
    if (SUCCEEDED(IPortableDeviceManager_GetDevices(mgr, ids, &count))) {
        for (DWORD i = 0; i < count; i++) {
            wchar_t name[256] = L"", maker[256] = L"", desc[256] = L"";
            DWORD n;
            n = 256; IPortableDeviceManager_GetDeviceFriendlyName(mgr, ids[i], name, &n);
            n = 256; IPortableDeviceManager_GetDeviceManufacturer(mgr, ids[i], maker, &n);
            n = 256; IPortableDeviceManager_GetDeviceDescription(mgr, ids[i], desc, &n);
            fputs("{\"id\":", stdout); put_json_string(ids[i]);
            fputs(",\"name\":", stdout); put_json_string(name);
            fputs(",\"maker\":", stdout); put_json_string(maker);
            fputs(",\"desc\":", stdout); put_json_string(desc);
            fputs("}\n", stdout);
            CoTaskMemFree(ids[i]);
        }
    }
    free(ids);
    IPortableDeviceManager_Release(mgr);
    fflush(stdout);
    return 0;
}

/* -------------------------------------------------------------------- scan */

static double filetime_to_epoch(const FILETIME *ft)
{
    ULARGE_INTEGER u;
    u.LowPart = ft->dwLowDateTime; u.HighPart = ft->dwHighDateTime;
    return (double)(u.QuadPart / 10000000ULL) - 11644473600.0;
}

static double variant_date_to_epoch(const PROPVARIANT *v)
{
    SYSTEMTIME st; FILETIME ft;
    if (v->vt == VT_DATE && VariantTimeToSystemTime(v->date, &st) && SystemTimeToFileTime(&st, &ft))
        return filetime_to_epoch(&ft);
    if (v->vt == VT_FILETIME) return filetime_to_epoch(&v->filetime);
    return 0;
}

static int g_files = 0;

static HRESULT g_last_hr = S_OK;        /* why the last picture could not be saved */

static void walk(IPortableDeviceContent *content, IPortableDeviceProperties *props,
                 IPortableDeviceKeyCollection *keys, const wchar_t *parent,
                 const wchar_t *path, int depth)
{
    IEnumPortableDeviceObjectIDs *en = NULL;
    if (depth > MAX_DEPTH ||
        FAILED(IPortableDeviceContent_EnumObjects(content, 0, parent, NULL, &en)))
        return;
    for (;;) {
        PWSTR ids[32];
        DWORD got = 0;
        HRESULT hr = IEnumPortableDeviceObjectIDs_Next(en, 32, ids, &got);
        if (FAILED(hr) || got == 0) break;
        for (DWORD i = 0; i < got; i++) {
            IPortableDeviceValues *v = NULL;
            wchar_t *name = NULL, *orig = NULL;
            GUID type = {0, 0, 0, {0, 0, 0, 0, 0, 0, 0, 0}};
            ULONGLONG size = 0, duration = 0;
            ULONG width = 0, height = 0;
            PROPVARIANT created, modified;
            PropVariantInit(&created); PropVariantInit(&modified);
            if (SUCCEEDED(IPortableDeviceProperties_GetValues(props, ids[i], keys, &v)) && v) {
                IPortableDeviceValues_GetStringValue(v, &WPD_OBJECT_NAME, &name);
                IPortableDeviceValues_GetStringValue(v, &WPD_OBJECT_ORIGINAL_FILE_NAME, &orig);
                IPortableDeviceValues_GetGuidValue(v, &WPD_OBJECT_CONTENT_TYPE, &type);
                IPortableDeviceValues_GetUnsignedLargeIntegerValue(v, &WPD_OBJECT_SIZE, &size);
                IPortableDeviceValues_GetUnsignedIntegerValue(v, &WPD_MEDIA_WIDTH, &width);
                IPortableDeviceValues_GetUnsignedIntegerValue(v, &WPD_MEDIA_HEIGHT, &height);
                IPortableDeviceValues_GetUnsignedLargeIntegerValue(v, &WPD_MEDIA_DURATION, &duration);
                IPortableDeviceValues_GetValue(v, &WPD_OBJECT_DATE_CREATED, &created);
                IPortableDeviceValues_GetValue(v, &WPD_OBJECT_DATE_MODIFIED, &modified);
            }
            const wchar_t *label = orig ? orig : (name ? name : L"");
            int is_folder = IsEqualGUID(&type, &WPD_CONTENT_TYPE_FOLDER) ||
                            IsEqualGUID(&type, &WPD_CONTENT_TYPE_FUNCTIONAL_OBJECT);
            if (is_folder) {
                if (label[0] != L'.' && _wcsicmp(label, L"Android") != 0 && _wcsicmp(label, L"LOST.DIR") != 0) {
                    wchar_t sub[4096];
                    if (IsEqualGUID(&type, &WPD_CONTENT_TYPE_FUNCTIONAL_OBJECT) && depth == 0)
                        swprintf(sub, 4096, L"%ls", label);      /* "Internal storage" */
                    else
                        swprintf(sub, 4096, L"%ls%ls%ls", path, path[0] ? L"/" : L"", label);
                    walk(content, props, keys, ids[i], sub, depth + 1);
                }
            } else if (label[0] && label[0] != L'.' && g_files < MAX_FILES) {
                double t = variant_date_to_epoch(&created);
                if (t <= 0) t = variant_date_to_epoch(&modified);
                fputs("{\"o\":", stdout); put_json_string(ids[i]);
                fputs(",\"n\":", stdout); put_json_string(label);
                fputs(",\"d\":", stdout); put_json_string(path);
                printf(",\"s\":%llu,\"t\":%.0f,\"w\":%lu,\"h\":%lu,\"u\":%llu}\n", size, t,
                       (unsigned long)width, (unsigned long)height, duration);
                fflush(stdout);
                g_files++;
            }
            if (g_files % 50 == 0) fflush(stdout);
            PropVariantClear(&created); PropVariantClear(&modified);
            if (name) CoTaskMemFree(name);
            if (orig) CoTaskMemFree(orig);
            if (v) IPortableDeviceValues_Release(v);
            CoTaskMemFree(ids[i]);
        }
    }
    IEnumPortableDeviceObjectIDs_Release(en);
}

static int cmd_scan(const wchar_t *id)
{
    IPortableDevice *dev = open_device(id, GENERIC_READ);
    IPortableDeviceContent *content = NULL;
    IPortableDeviceProperties *props = NULL;
    IPortableDeviceKeyCollection *keys = NULL;
    if (!dev) return 1;
    if (FAILED(IPortableDevice_Content(dev, &content)) ||
        FAILED(IPortableDeviceContent_Properties(content, &props)) ||
        FAILED(CoCreateInstance(&CLSID_PortableDeviceKeyCollection, NULL, CLSCTX_INPROC_SERVER,
                                &IID_IPortableDeviceKeyCollection, (void **)&keys))) {
        error_line("content", E_FAIL);
        return 1;
    }
    IPortableDeviceKeyCollection_Add(keys, &WPD_OBJECT_NAME);
    IPortableDeviceKeyCollection_Add(keys, &WPD_OBJECT_ORIGINAL_FILE_NAME);
    IPortableDeviceKeyCollection_Add(keys, &WPD_OBJECT_CONTENT_TYPE);
    IPortableDeviceKeyCollection_Add(keys, &WPD_OBJECT_SIZE);
    IPortableDeviceKeyCollection_Add(keys, &WPD_OBJECT_DATE_CREATED);
    IPortableDeviceKeyCollection_Add(keys, &WPD_OBJECT_DATE_MODIFIED);
    IPortableDeviceKeyCollection_Add(keys, &WPD_MEDIA_WIDTH);
    IPortableDeviceKeyCollection_Add(keys, &WPD_MEDIA_HEIGHT);
    IPortableDeviceKeyCollection_Add(keys, &WPD_MEDIA_DURATION);
    walk(content, props, keys, WPD_DEVICE_OBJECT_ID, L"", 0);
    fputs("{\"done\":true}\n", stdout);
    fflush(stdout);
    IPortableDeviceKeyCollection_Release(keys);
    IPortableDeviceProperties_Release(props);
    IPortableDeviceContent_Release(content);
    IPortableDevice_Close(dev);
    IPortableDevice_Release(dev);
    return 0;
}

/* ---------------------------------------------------------- thumbs / fetch */

/* One object's resource (the file itself, or its thumbnail) into a file. */
static int save_resource(IPortableDeviceResources *res, const wchar_t *object,
                         const PROPERTYKEY *key, const wchar_t *out)
{
    IStream *stream = NULL;
    DWORD buffer = 0;
    HANDLE f;
    BYTE *data;
    int ok = 0;
    HRESULT opened = IPortableDeviceResources_GetStream(res, object, key, STGM_READ, &buffer, &stream);
    if (FAILED(opened) || !stream) { g_last_hr = FAILED(opened) ? opened : E_FAIL; return 0; }
    if (buffer < 65536) buffer = 1 << 20;
    data = malloc(buffer);
    wchar_t part[4096];
    swprintf(part, 4096, L"%ls.part", out);
    f = CreateFileW(part, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) g_last_hr = HRESULT_FROM_WIN32(GetLastError());
    if (f != INVALID_HANDLE_VALUE) {
        ULONG got = 0;
        HRESULT hr;
        ok = 1;
        for (;;) {
            DWORD wrote;
            hr = IStream_Read(stream, data, buffer, &got);
            if (FAILED(hr)) { ok = 0; g_last_hr = hr; break; }
            if (got == 0) break;
            if (!WriteFile(f, data, got, &wrote, NULL) || wrote != got) { ok = 0; break; }
        }
        CloseHandle(f);
        if (ok) ok = MoveFileExW(part, out, MOVEFILE_REPLACE_EXISTING) != 0;
        if (!ok) DeleteFileW(part);
    }
    free(data);
    IStream_Release(stream);
    return ok;
}

/* The whole photo, saved as a file: Piklin makes a sharp picture from it, turned the way the photo is
 * turned (the phone's own small picture is tiny and ignores that). Read to the end - a transfer left
 * half done leaves the phone answering with the wrong file. */
static void save_full(IPortableDeviceResources *res, const wchar_t *object, const wchar_t *out)
{
    save_resource(res, object, &WPD_RESOURCE_DEFAULT, out);
}

static IPortableDeviceResources *resources_of(IPortableDevice *dev, IPortableDeviceContent **content)
{
    IPortableDeviceResources *res = NULL;
    if (FAILED(IPortableDevice_Content(dev, content))) return NULL;
    if (FAILED(IPortableDeviceContent_Transfer(*content, &res))) return NULL;
    return res;
}

static int cmd_thumbs(const wchar_t *id)
{
    IPortableDevice *dev = open_device(id, GENERIC_READ);
    IPortableDeviceContent *content = NULL;
    IPortableDeviceResources *res;
    wchar_t line[8192];
    if (!dev) return 1;
    res = resources_of(dev, &content);
    if (!res) { error_line("resources", E_FAIL); return 1; }
    while (fgetws(line, 8192, stdin)) {
        wchar_t *tab = wcschr(line, L'\t'), *end, *head = NULL;
        if (!tab) continue;
        *tab = 0;
        end = tab + 1 + wcscspn(tab + 1, L"\r\n");
        *end = 0;
        /* A third column asks for the whole photo as well, saved there. */
        head = wcschr(tab + 1, L'\t');
        if (head) *head++ = 0;
        int ok;
        g_last_hr = S_OK;
        ok = save_resource(res, line, &WPD_RESOURCE_THUMBNAIL, tab + 1);
        if (ok && head) save_full(res, line, head);
        printf("{\"o\":"); put_json_string(line);
        /* Why there is no picture: 0x80070141 is a phone that locked itself. */
        if (ok) printf(",\"ok\":true}\n");
        else printf(",\"ok\":false,\"e\":\"0x%08lx\"}\n", (unsigned long)g_last_hr);
        fflush(stdout);
    }
    IPortableDeviceResources_Release(res);
    IPortableDeviceContent_Release(content);
    IPortableDevice_Close(dev);
    IPortableDevice_Release(dev);
    return 0;
}

static int cmd_fetch(const wchar_t *id, const wchar_t *object, const wchar_t *out)
{
    IPortableDevice *dev = open_device(id, GENERIC_READ);
    IPortableDeviceContent *content = NULL;
    IPortableDeviceResources *res;
    int ok;
    if (!dev) return 1;
    res = resources_of(dev, &content);
    if (!res) { error_line("resources", E_FAIL); return 1; }
    ok = save_resource(res, object, &WPD_RESOURCE_DEFAULT, out);
    IPortableDeviceResources_Release(res);
    IPortableDeviceContent_Release(content);
    IPortableDevice_Close(dev);
    IPortableDevice_Release(dev);
    return ok ? 0 : 1;
}

int wmain(int argc, wchar_t **argv)
{
    int rc = 2;
    _setmode(_fileno(stdout), _O_BINARY);
    if (argc < 2) return 2;
    if (FAILED(CoInitializeEx(NULL, COINIT_MULTITHREADED))) return 1;
    if (!wcscmp(argv[1], L"list")) rc = cmd_list();
    else if (!wcscmp(argv[1], L"scan") && argc >= 3) rc = cmd_scan(argv[2]);
    else if (!wcscmp(argv[1], L"thumbs") && argc >= 3) rc = cmd_thumbs(argv[2]);
    else if (!wcscmp(argv[1], L"fetch") && argc >= 5) rc = cmd_fetch(argv[2], argv[3], argv[4]);
    CoUninitialize();
    return rc;
}
