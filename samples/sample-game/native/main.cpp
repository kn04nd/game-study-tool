// Sample game entry point.
//   SampleGame.exe                         window: H heals, F5 saves, F9 loads, R restarts, Esc quits
//   SampleGame.exe --frames 300            window that closes itself (frame captures, traces)
//   SampleGame.exe --frames 200 --capture-frame 100
//                                          under RenderDoc: capture frame 100 through its in-app API
//   SampleGame.exe --headless --ticks 600 --save run.sgs --timestamp 1700000000
//                                          no window: simulate, save, print a JSON summary
//   SampleGame.exe --headless --realtime --ticks 3600
//                                          no window, 60 ticks per second of wall time (runtime tools)
// Options: --load <file.sgs>, --settings <settings.ini> (default: next to the exe)
#include <windows.h>

#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <string>

#include "game.h"
#include "render.h"
#include "save.h"
#include "third_party/renderdoc_app.h"

namespace {

constexpr float kStep = 1.0f / 60.0f;

struct Options {
    bool headless = false;
    bool realtime = false;
    long ticks = 600;
    long frames = -1;
    long captureFrame = -1;
    std::wstring save, load, settings;
    unsigned long long timestamp = 0;
};

std::wstring ExeDir() {
    wchar_t path[MAX_PATH];
    DWORD n = GetModuleFileNameW(nullptr, path, MAX_PATH);
    std::wstring dir(path, n);
    return dir.substr(0, dir.find_last_of(L"\\/") + 1);
}

std::wstring DefaultSave() {
    wchar_t* base = nullptr;
    size_t length = 0;
    std::wstring dir = L".";
    if (_wdupenv_s(&base, &length, L"LOCALAPPDATA") == 0 && base != nullptr) {
        dir = std::wstring(base) + L"\\SampleGame";
        free(base);
    }
    CreateDirectoryW(dir.c_str(), nullptr);
    return dir + L"\\save.sgs";
}

Options Parse(int argc, wchar_t** argv) {
    Options o;
    for (int i = 1; i < argc; i++) {
        std::wstring a = argv[i];
        bool more = i + 1 < argc;
        if (a == L"--headless") o.headless = true;
        else if (a == L"--realtime") o.realtime = true;
        else if (a == L"--ticks" && more) o.ticks = std::wcstol(argv[++i], nullptr, 10);
        else if (a == L"--frames" && more) o.frames = std::wcstol(argv[++i], nullptr, 10);
        else if (a == L"--capture-frame" && more) o.captureFrame = std::wcstol(argv[++i], nullptr, 10);
        else if (a == L"--save" && more) o.save = argv[++i];
        else if (a == L"--load" && more) o.load = argv[++i];
        else if (a == L"--settings" && more) o.settings = argv[++i];
        else if (a == L"--timestamp" && more) o.timestamp = std::wcstoull(argv[++i], nullptr, 10);
    }
    if (o.settings.empty()) o.settings = ExeDir() + L"settings.ini";
    if (o.save.empty() && !o.headless) o.save = DefaultSave();
    if (o.timestamp == 0) o.timestamp = static_cast<unsigned long long>(std::time(nullptr));
    return o;
}

int RunHeadless(sg::World& world, const Options& o) {
    for (long i = 0; i < o.ticks; i++) {
        world.Step(kStep);
        if (o.realtime) {
            Sleep(16);  // about 60 ticks per second, so tools can watch it live
        }
    }
    bool saved = !o.save.empty() && sg::SaveWorld(world, o.save, o.timestamp);
    const sg::Player& p = world.PlayerRef();
    std::printf("{\"tick\": %u, \"hp\": %d, \"max_hp\": %d, \"score\": %u, \"alive\": %s, \"saved\": %s}\n", world.tick, p.hp,
                p.maxHp, p.score, p.Alive() ? "true" : "false", saved ? "true" : "false");
    return 0;
}

sg::World* g_world = nullptr;
const Options* g_options = nullptr;

// RenderDoc's in-application API, present only when the game was launched under RenderDoc.
RENDERDOC_API_1_1_2* RenderDocApi() {
    HMODULE module = GetModuleHandleW(L"renderdoc.dll");
    if (module == nullptr) {
        return nullptr;
    }
    auto getApi = reinterpret_cast<pRENDERDOC_GetAPI>(reinterpret_cast<void*>(GetProcAddress(module, "RENDERDOC_GetAPI")));
    RENDERDOC_API_1_1_2* api = nullptr;
    if (getApi == nullptr || getApi(eRENDERDOC_API_Version_1_1_2, reinterpret_cast<void**>(&api)) != 1) {
        return nullptr;
    }
    return api;
}

LRESULT CALLBACK WindowProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_KEYDOWN:
            if (wp == VK_ESCAPE) {
                DestroyWindow(hwnd);
            } else if (wp == 'H') {
                g_world->PlayerRef().Heal(10);
            } else if (wp == 'R') {
                g_world->Reset();
            } else if (wp == VK_F5) {
                sg::SaveWorld(*g_world, g_options->save, static_cast<unsigned long long>(std::time(nullptr)));
            } else if (wp == VK_F9) {
                sg::LoadWorld(*g_world, g_options->save);
            }
            return 0;
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
        default:
            return DefWindowProcW(hwnd, msg, wp, lp);
    }
}

int RunWindow(sg::World& world, const Options& o) {
    g_world = &world;
    g_options = &o;
    WNDCLASSW wc = {};
    wc.lpfnWndProc = WindowProc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.hCursor = LoadCursorW(nullptr, reinterpret_cast<LPCWSTR>(IDC_ARROW));
    wc.lpszClassName = L"SampleGameWindow";
    RegisterClassW(&wc);
    RECT rect = {0, 0, 960, 540};
    AdjustWindowRect(&rect, WS_OVERLAPPEDWINDOW, FALSE);
    HWND hwnd = CreateWindowW(wc.lpszClassName, L"Sample Game", WS_OVERLAPPEDWINDOW & ~WS_THICKFRAME, CW_USEDEFAULT,
                              CW_USEDEFAULT, rect.right - rect.left, rect.bottom - rect.top, nullptr, nullptr, wc.hInstance,
                              nullptr);
    sg::Renderer renderer;
    if (hwnd == nullptr || !renderer.Init(hwnd, 960, 540)) {
        std::fprintf(stderr, "Direct3D 11 initialisation failed\n");
        return 1;
    }
    ShowWindow(hwnd, SW_SHOW);
    LARGE_INTEGER frequency, last, now;
    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&last);
    float accumulator = 0, elapsed = 0;
    long frame = 0;
    MSG msg = {};
    while (msg.message != WM_QUIT) {
        if (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
            continue;
        }
        QueryPerformanceCounter(&now);
        float dt = static_cast<float>(now.QuadPart - last.QuadPart) / static_cast<float>(frequency.QuadPart);
        last = now;
        accumulator += dt > 0.25f ? 0.25f : dt;
        elapsed += dt;
        while (accumulator >= kStep) {
            world.Step(kStep);
            accumulator -= kStep;
        }
        if (frame + 1 == o.captureFrame) {
            if (RENDERDOC_API_1_1_2* api = RenderDocApi()) {
                api->TriggerCapture();  // the next presented frame is captured
            }
        }
        renderer.Draw(world, elapsed);
        if (++frame % 30 == 0) {
            const sg::Player& p = world.PlayerRef();
            wchar_t title[128];
            swprintf_s(title, L"Sample Game - HP %d/%d - score %u%s", p.hp, p.maxHp, p.score, p.Alive() ? L"" : L" - DEAD (R)");
            SetWindowTextW(hwnd, title);
        }
        if (o.frames > 0 && frame >= o.frames) {
            DestroyWindow(hwnd);
        }
    }
    return 0;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    Options options = Parse(argc, argv);
    sg::World world(sg::LoadSettings(options.settings));
    if (!options.load.empty() && !sg::LoadWorld(world, options.load)) {
        std::fprintf(stderr, "could not load the save (missing, corrupted or another version)\n");
        return 2;
    }
    return options.headless ? RunHeadless(world, options) : RunWindow(world, options);
}
