/* Piklin.exe: starts Python (the one in this folder) on piklin.app.
 *
 * It sits in runtime\bin beside libpython, so Windows shows Piklin's name and
 * icon in the taskbar, and so sys.executable - which Piklin uses to start
 * itself again after "Open Library..." - is this program, which accepts the
 * same arguments as python.exe. With no single-dash option first (-m, -s, -c), it runs piklin.app.
 *
 * Build: gcc -O2 -mwindows -municode launcher.c icon.o -lpython3.14
 */
#include <windows.h>
#include <wchar.h>
#include <stdlib.h>
#include <math.h>
#include <Python.h>

static void set_env(const wchar_t *name, const wchar_t *dir, const wchar_t *tail)
{
    wchar_t v[MAX_PATH * 2];
    swprintf(v, MAX_PATH * 2, L"%ls%ls", dir, tail);
    _wputenv_s(name, v);       /* the C runtime's copy, which Python reads */
}

/* ---------------------------------------------------------------- splash
 * Loading Python, GTK and the libraries takes a second or two, and several
 * more the first time after an install, while Windows checks the new files.
 * A window with the release's banner is up from the first instant, and goes
 * away when Piklin's own window appears. */
static HWND g_splash;
static DWORD g_pid;
static HBITMAP g_image;        /* resource 2: the banner picture */

static BOOL CALLBACK find_main(HWND w, LPARAM found)
{
    DWORD pid;
    RECT r;
    GetWindowThreadProcessId(w, &pid);
    if (pid != g_pid || w == g_splash || !IsWindowVisible(w)) return TRUE;
    GetWindowRect(w, &r);
    if (r.right - r.left > 400 && r.bottom - r.top > 300) { *(int *)found = 1; return FALSE; }
    return TRUE;
}

static LRESULT CALLBACK splash_proc(HWND w, UINT msg, WPARAM wp, LPARAM lp)
{
    static int ticks = 0, seen = 0;
    switch (msg) {
    case WM_PAINT: {
        PAINTSTRUCT ps;
        RECT r;
        HDC dc = BeginPaint(w, &ps);
        GetClientRect(w, &r);
        if (g_image) {
            /* The banner of the release (docs/screenshots/hero.png), scaled to the window. */
            HDC mem = CreateCompatibleDC(dc);
            HGDIOBJ old = SelectObject(mem, g_image);
            BITMAP bm;
            GetObject(g_image, sizeof bm, &bm);
            SetStretchBltMode(dc, HALFTONE);
            SetBrushOrgEx(dc, 0, 0, NULL);
            StretchBlt(dc, 0, 0, r.right, r.bottom, mem, 0, 0, bm.bmWidth, bm.bmHeight, SRCCOPY);
            SelectObject(mem, old);
            DeleteDC(mem);
        } else {
            HBRUSH bg = CreateSolidBrush(RGB(20, 16, 48));
            FillRect(dc, &r, bg);
            DeleteObject(bg);
        }
        EndPaint(w, &ps);
        return 0;
    }
    case WM_ERASEBKGND:
        return 1;
    case WM_TIMER:
        /* Piklin's window is up: leave one more beat for it to paint, then go. */
        if (!seen) EnumWindows(find_main, (LPARAM)&seen);
        else if (seen++ > 3) PostQuitMessage(0);
        if (++ticks > 1200) PostQuitMessage(0);        /* a minute: never stay for ever */
        return 0;
    }
    return DefWindowProcW(w, msg, wp, lp);
}

/* The banner as the window's picture, with rounded corners: a layered window with a
 * transparency value for every pixel, so the corners are smooth (not the jagged edge
 * of a window region) on Windows 10 and 11 alike. */
static void render_splash(HWND win, int w, int h, UINT dpi)
{
    BITMAPINFO bi = {0};
    HDC screen = GetDC(NULL), mem = CreateCompatibleDC(screen);
    unsigned char *px = NULL;
    HBITMAP dib;
    HGDIOBJ old;
    double radius = 26.0 * dpi / 96.0;
    POINT at = {0, 0}, pos;
    SIZE size = {w, h};
    BLENDFUNCTION blend = {AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};
    RECT where;

    bi.bmiHeader.biSize = sizeof bi.bmiHeader;
    bi.bmiHeader.biWidth = w;
    bi.bmiHeader.biHeight = -h;                      /* top-down */
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    dib = CreateDIBSection(screen, &bi, DIB_RGB_COLORS, (void **)&px, NULL, 0);
    if (!dib || !px) { DeleteDC(mem); ReleaseDC(NULL, screen); return; }
    old = SelectObject(mem, dib);
    if (g_image) {
        HDC src = CreateCompatibleDC(screen);
        HGDIOBJ prev = SelectObject(src, g_image);
        BITMAP bm;
        GetObject(g_image, sizeof bm, &bm);
        SetStretchBltMode(mem, HALFTONE);
        SetBrushOrgEx(mem, 0, 0, NULL);
        StretchBlt(mem, 0, 0, w, h, src, 0, 0, bm.bmWidth, bm.bmHeight, SRCCOPY);
        SelectObject(src, prev);
        DeleteDC(src);
    } else {
        for (int i = 0; i < w * h; i++) { px[i * 4] = 48; px[i * 4 + 1] = 16; px[i * 4 + 2] = 20; }
    }
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            double cx = x + 0.5 < radius ? radius : (x + 0.5 > w - radius ? w - radius : x + 0.5);
            double cy = y + 0.5 < radius ? radius : (y + 0.5 > h - radius ? h - radius : y + 0.5);
            double dx = x + 0.5 - cx, dy = y + 0.5 - cy;
            double a = radius - sqrt(dx * dx + dy * dy) + 0.5;     /* how much of the pixel is inside */
            unsigned char *p = px + ((size_t)y * w + x) * 4;
            if (a >= 1.0) { p[3] = 255; continue; }
            if (a < 0.0) a = 0.0;
            p[0] = (unsigned char)(p[0] * a); p[1] = (unsigned char)(p[1] * a);
            p[2] = (unsigned char)(p[2] * a); p[3] = (unsigned char)(255 * a);   /* premultiplied */
        }
    }
    GetWindowRect(win, &where);
    pos.x = where.left; pos.y = where.top;
    UpdateLayeredWindow(win, screen, &pos, &size, mem, &at, 0, &blend, ULW_ALPHA);
    SelectObject(mem, old);
    DeleteObject(dib);
    DeleteDC(mem);
    ReleaseDC(NULL, screen);
}

static DWORD WINAPI splash_thread(LPVOID unused)
{
    WNDCLASSW wc = {0};
    MSG m;
    UINT dpi = GetDpiForSystem();
    int w = (int)(720 * dpi / 96), h = (int)(360 * dpi / 96);     /* the banner is twice as wide as tall */
    g_image = (HBITMAP)LoadImageW(GetModuleHandleW(NULL), MAKEINTRESOURCEW(2), IMAGE_BITMAP, 0, 0, LR_CREATEDIBSECTION);
    wc.lpfnWndProc = splash_proc;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.lpszClassName = L"PiklinSplash";
    wc.hCursor = LoadCursorW(NULL, IDC_ARROW);
    RegisterClassW(&wc);
    g_splash = CreateWindowExW(WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE, wc.lpszClassName, L"", WS_POPUP,
                               (GetSystemMetrics(SM_CXSCREEN) - w) / 2, (GetSystemMetrics(SM_CYSCREEN) - h) / 2,
                               w, h, NULL, NULL, wc.hInstance, NULL);
    if (!g_splash) return 0;
    render_splash(g_splash, w, h, dpi);
    ShowWindow(g_splash, SW_SHOWNOACTIVATE);
    SetTimer(g_splash, 1, 50, NULL);
    while (GetMessageW(&m, NULL, 0, 0) > 0) { TranslateMessage(&m); DispatchMessageW(&m); }
    DestroyWindow(g_splash);
    g_splash = NULL;
    return 0;
}

int WINAPI wWinMain(HINSTANCE h, HINSTANCE p, PWSTR cmd, int show)
{
    /* Sharp on a screen scaled to 150 or 200 %, as GTK will be. */
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    wchar_t exe[MAX_PATH], bin[MAX_PATH], root[MAX_PATH], runtime[MAX_PATH], path[32768];
    GetModuleFileNameW(NULL, exe, MAX_PATH);
    wcscpy(bin, exe);
    *wcsrchr(bin, L'\\') = 0;                     /* runtime\bin */
    wcscpy(runtime, bin);
    *wcsrchr(runtime, L'\\') = 0;                 /* runtime */
    wcscpy(root, runtime);
    *wcsrchr(root, L'\\') = 0;                    /* the Piklin folder */

    set_env(L"PIKLIN_RUNTIME", runtime, L"");
    set_env(L"PIKLIN_APPDIR", root, L"");
    set_env(L"PYTHONPATH", root, L"\\app");
    set_env(L"SSL_CERT_FILE", runtime, L"\\etc\\ssl\\certs\\ca-bundle.crt");
    _wputenv_s(L"PYTHONNOUSERSITE", L"1");
    _wputenv_s(L"PYTHONDONTWRITEBYTECODE", L"1");     /* nothing is written under Program Files */
    _wputenv_s(L"PYTHONHOME", L"");
    /* The libraries Piklin carries come first, for it and for what it starts. */
    DWORD n = GetEnvironmentVariableW(L"PATH", path + wcslen(bin) + 1, 32768 - (DWORD)wcslen(bin) - 2);
    wcscpy(path, bin);
    path[wcslen(bin)] = n ? L';' : 0;
    _wputenv_s(L"PATH", path);
    SetDllDirectoryW(bin);

    int argc = __argc, k = 0;
    wchar_t **in = __wargv, **argv = calloc(argc + 5, sizeof(wchar_t *));
    argv[k++] = exe;
    /* -P: do not put the folder Piklin was opened from first on the module path. Without
     * it a "piklin" folder there (a source checkout, a download) would be loaded in place
     * of the installed program. */
    argv[k++] = L"-P";
    if (argc < 2 || !(in[1][0] == L'-' && in[1][1] != L'-')) {
        argv[k++] = L"-m";
        argv[k++] = L"piklin.app";
        g_pid = GetCurrentProcessId();
        CloseHandle(CreateThread(NULL, 0, splash_thread, NULL, 0, NULL));   /* only for the app itself */
    }
    for (int i = 1; i < argc; i++) argv[k++] = in[i];
    return Py_Main(k, argv);
}
