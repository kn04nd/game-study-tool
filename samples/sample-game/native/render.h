// Two-pass D3D11 renderer: the arena into an offscreen target (depth tested, lit cubes), then a full-screen
// post pass (vignette, red tint as the player loses HP) into the swap chain.
#pragma once

#include <windows.h>

#include <d3d11.h>
#include <wrl/client.h>

#include "game.h"

namespace sg {

class Renderer {
public:
    bool Init(HWND window, UINT width, UINT height);
    void Draw(const World& world, float time);

private:
    bool CreateTargets();
    bool CreateShaders();
    bool CreateGeometry();
    void DrawBox(ID3D11DeviceContext* ctx, float x, float y, float z, float sx, float sy, float sz, const float color[4]);

    UINT width_ = 0, height_ = 0;
    Microsoft::WRL::ComPtr<ID3D11Device> device_;
    Microsoft::WRL::ComPtr<ID3D11DeviceContext> context_;
    Microsoft::WRL::ComPtr<IDXGISwapChain> swapChain_;
    Microsoft::WRL::ComPtr<ID3D11RenderTargetView> backBuffer_;
    Microsoft::WRL::ComPtr<ID3D11Texture2D> sceneTexture_;
    Microsoft::WRL::ComPtr<ID3D11RenderTargetView> sceneTarget_;
    Microsoft::WRL::ComPtr<ID3D11ShaderResourceView> sceneView_;
    Microsoft::WRL::ComPtr<ID3D11DepthStencilView> depth_;
    Microsoft::WRL::ComPtr<ID3D11VertexShader> sceneVS_, postVS_;
    Microsoft::WRL::ComPtr<ID3D11PixelShader> scenePS_, postPS_;
    Microsoft::WRL::ComPtr<ID3D11InputLayout> layout_;
    Microsoft::WRL::ComPtr<ID3D11Buffer> vertices_, indices_, frameConstants_, objectConstants_;
    Microsoft::WRL::ComPtr<ID3D11SamplerState> sampler_;
};

}  // namespace sg
