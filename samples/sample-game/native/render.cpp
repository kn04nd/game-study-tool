#include "render.h"

#include <DirectXMath.h>
#include <d3dcompiler.h>

#include <cmath>
#include <cstring>

using Microsoft::WRL::ComPtr;
using namespace DirectX;

namespace sg {

namespace {

// Compiled at start-up with D3DCompile, so the source also shows up in a frame capture.
const char kShaders[] = R"HLSL(
cbuffer Frame : register(b0) { float4x4 viewProj; float4 lightDir; float4 tint; };
cbuffer Object : register(b1) { float4x4 world; float4 color; };

struct VSIn { float3 pos : POSITION; float3 normal : NORMAL; };
struct VSOut { float4 pos : SV_Position; float3 normal : NORMAL; float4 color : COLOR; };

VSOut SceneVS(VSIn i) {
    VSOut o;
    float4 wp = mul(float4(i.pos, 1), world);
    o.pos = mul(wp, viewProj);
    o.normal = mul(float4(i.normal, 0), world).xyz;
    o.color = color;
    return o;
}

float4 ScenePS(VSOut i) : SV_Target {
    float light = saturate(dot(normalize(i.normal), -lightDir.xyz)) * 0.8 + 0.2;
    return float4(i.color.rgb * light, 1);
}

Texture2D scene : register(t0);
SamplerState linearClamp : register(s0);
struct PostOut { float4 pos : SV_Position; float2 uv : TEXCOORD0; };

PostOut PostVS(uint id : SV_VertexID) {
    PostOut o;
    o.uv = float2((id << 1) & 2, id & 2);
    o.pos = float4(o.uv * float2(2, -2) + float2(-1, 1), 0, 1);
    return o;
}

float4 PostPS(PostOut i) : SV_Target {
    float3 c = scene.Sample(linearClamp, i.uv).rgb;
    float2 d = i.uv - 0.5;
    float vignette = 1 - dot(d, d) * 1.2;
    float hurt = tint.w;  // 0 at full HP, 1 when dead
    c = lerp(c, float3(min(c.r + 0.35, 1), c.g * 0.45, c.b * 0.45), hurt);
    return float4(c * vignette, 1);
}
)HLSL";

struct Vertex {
    float position[3];
    float normal[3];
};

struct FrameConstants {
    XMFLOAT4X4 viewProj;
    XMFLOAT4 lightDir;
    XMFLOAT4 tint;
};

struct ObjectConstants {
    XMFLOAT4X4 world;
    XMFLOAT4 color;
};

bool Compile(const char* entry, const char* target, ComPtr<ID3DBlob>& blob) {
    ComPtr<ID3DBlob> errors;
    HRESULT hr = D3DCompile(kShaders, sizeof(kShaders) - 1, "sample_game.hlsl", nullptr, nullptr, entry, target,
                            D3DCOMPILE_ENABLE_STRICTNESS, 0, &blob, &errors);
    if (FAILED(hr) && errors) {
        OutputDebugStringA(static_cast<const char*>(errors->GetBufferPointer()));
    }
    return SUCCEEDED(hr);
}

}  // namespace

bool Renderer::Init(HWND window, UINT width, UINT height) {
    width_ = width;
    height_ = height;
    DXGI_SWAP_CHAIN_DESC desc = {};
    desc.BufferCount = 2;
    desc.BufferDesc.Width = width;
    desc.BufferDesc.Height = height;
    desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.OutputWindow = window;
    desc.SampleDesc.Count = 1;
    desc.Windowed = TRUE;
    desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    D3D_FEATURE_LEVEL level = D3D_FEATURE_LEVEL_11_0;
    HRESULT hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, &level, 1, D3D11_SDK_VERSION,
                                               &desc, &swapChain_, &device_, nullptr, &context_);
    if (FAILED(hr)) {  // no GPU (VM, remote session): the software rasterizer still draws the same frame
        hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, &level, 1, D3D11_SDK_VERSION, &desc,
                                           &swapChain_, &device_, nullptr, &context_);
    }
    return SUCCEEDED(hr) && CreateTargets() && CreateShaders() && CreateGeometry();
}

bool Renderer::CreateTargets() {
    ComPtr<ID3D11Texture2D> back;
    if (FAILED(swapChain_->GetBuffer(0, IID_PPV_ARGS(&back))) ||
        FAILED(device_->CreateRenderTargetView(back.Get(), nullptr, &backBuffer_))) {
        return false;
    }
    D3D11_TEXTURE2D_DESC color = {};
    color.Width = width_;
    color.Height = height_;
    color.MipLevels = 1;
    color.ArraySize = 1;
    color.Format = DXGI_FORMAT_R16G16B16A16_FLOAT;
    color.SampleDesc.Count = 1;
    color.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    if (FAILED(device_->CreateTexture2D(&color, nullptr, &sceneTexture_)) ||
        FAILED(device_->CreateRenderTargetView(sceneTexture_.Get(), nullptr, &sceneTarget_)) ||
        FAILED(device_->CreateShaderResourceView(sceneTexture_.Get(), nullptr, &sceneView_))) {
        return false;
    }
    D3D11_TEXTURE2D_DESC depthDesc = color;
    depthDesc.Format = DXGI_FORMAT_D24_UNORM_S8_UINT;
    depthDesc.BindFlags = D3D11_BIND_DEPTH_STENCIL;
    ComPtr<ID3D11Texture2D> depthTexture;
    return SUCCEEDED(device_->CreateTexture2D(&depthDesc, nullptr, &depthTexture)) &&
           SUCCEEDED(device_->CreateDepthStencilView(depthTexture.Get(), nullptr, &depth_));
}

bool Renderer::CreateShaders() {
    ComPtr<ID3DBlob> sceneVS, scenePS, postVS, postPS;
    if (!Compile("SceneVS", "vs_5_0", sceneVS) || !Compile("ScenePS", "ps_5_0", scenePS) ||
        !Compile("PostVS", "vs_5_0", postVS) || !Compile("PostPS", "ps_5_0", postPS)) {
        return false;
    }
    device_->CreateVertexShader(sceneVS->GetBufferPointer(), sceneVS->GetBufferSize(), nullptr, &sceneVS_);
    device_->CreatePixelShader(scenePS->GetBufferPointer(), scenePS->GetBufferSize(), nullptr, &scenePS_);
    device_->CreateVertexShader(postVS->GetBufferPointer(), postVS->GetBufferSize(), nullptr, &postVS_);
    device_->CreatePixelShader(postPS->GetBufferPointer(), postPS->GetBufferSize(), nullptr, &postPS_);
    D3D11_INPUT_ELEMENT_DESC elements[] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"NORMAL", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 12, D3D11_INPUT_PER_VERTEX_DATA, 0},
    };
    if (FAILED(device_->CreateInputLayout(elements, 2, sceneVS->GetBufferPointer(), sceneVS->GetBufferSize(), &layout_))) {
        return false;
    }
    D3D11_BUFFER_DESC cb = {};
    cb.Usage = D3D11_USAGE_DEFAULT;
    cb.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    cb.ByteWidth = sizeof(FrameConstants);
    device_->CreateBuffer(&cb, nullptr, &frameConstants_);
    cb.ByteWidth = sizeof(ObjectConstants);
    device_->CreateBuffer(&cb, nullptr, &objectConstants_);
    D3D11_SAMPLER_DESC sampler = {};
    sampler.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sampler.AddressU = sampler.AddressV = sampler.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sampler.MaxLOD = D3D11_FLOAT32_MAX;
    return SUCCEEDED(device_->CreateSamplerState(&sampler, &sampler_));
}

bool Renderer::CreateGeometry() {
    // unit cube: 6 faces x 4 vertices with the face normal
    const float n[6][3] = {{0, 0, -1}, {0, 0, 1}, {-1, 0, 0}, {1, 0, 0}, {0, 1, 0}, {0, -1, 0}};
    const float c[6][4][3] = {
        {{-1, -1, -1}, {-1, 1, -1}, {1, 1, -1}, {1, -1, -1}}, {{1, -1, 1}, {1, 1, 1}, {-1, 1, 1}, {-1, -1, 1}},
        {{-1, -1, 1}, {-1, 1, 1}, {-1, 1, -1}, {-1, -1, -1}}, {{1, -1, -1}, {1, 1, -1}, {1, 1, 1}, {1, -1, 1}},
        {{-1, 1, -1}, {-1, 1, 1}, {1, 1, 1}, {1, 1, -1}},     {{-1, -1, 1}, {-1, -1, -1}, {1, -1, -1}, {1, -1, 1}},
    };
    Vertex vertices[24];
    uint16_t indices[36];
    for (int f = 0; f < 6; f++) {
        for (int v = 0; v < 4; v++) {
            Vertex& out = vertices[f * 4 + v];
            for (int k = 0; k < 3; k++) {
                out.position[k] = c[f][v][k] * 0.5f;
                out.normal[k] = n[f][k];
            }
        }
        const uint16_t base = static_cast<uint16_t>(f * 4);
        const uint16_t quad[6] = {0, 1, 2, 0, 2, 3};
        for (int i = 0; i < 6; i++) {
            indices[f * 6 + i] = static_cast<uint16_t>(base + quad[i]);
        }
    }
    D3D11_BUFFER_DESC vb = {sizeof(vertices), D3D11_USAGE_IMMUTABLE, D3D11_BIND_VERTEX_BUFFER, 0, 0, 0};
    D3D11_SUBRESOURCE_DATA vdata = {vertices, 0, 0};
    D3D11_BUFFER_DESC ib = {sizeof(indices), D3D11_USAGE_IMMUTABLE, D3D11_BIND_INDEX_BUFFER, 0, 0, 0};
    D3D11_SUBRESOURCE_DATA idata = {indices, 0, 0};
    return SUCCEEDED(device_->CreateBuffer(&vb, &vdata, &vertices_)) && SUCCEEDED(device_->CreateBuffer(&ib, &idata, &indices_));
}

void Renderer::DrawBox(ID3D11DeviceContext* ctx, float x, float y, float z, float sx, float sy, float sz, const float color[4]) {
    ObjectConstants object;
    XMStoreFloat4x4(&object.world, XMMatrixTranspose(XMMatrixScaling(sx, sy, sz) * XMMatrixTranslation(x, y, z)));
    object.color = XMFLOAT4(color[0], color[1], color[2], color[3]);
    ctx->UpdateSubresource(objectConstants_.Get(), 0, nullptr, &object, 0, 0);
    ctx->DrawIndexed(36, 0, 0);
}

void Renderer::Draw(const World& world, float time) {
    ID3D11DeviceContext* ctx = context_.Get();
    const Player& player = world.PlayerRef();

    // pass 1: the arena into the HDR scene target
    FrameConstants frame;
    XMVECTOR eye = XMVectorSet(std::sin(time * 0.1f) * 3.0f, 9.0f, -11.0f, 1.0f);
    XMMATRIX view = XMMatrixLookAtLH(eye, XMVectorSet(player.pos.x, 0, player.pos.z, 1), XMVectorSet(0, 1, 0, 0));
    XMMATRIX proj = XMMatrixPerspectiveFovLH(XMConvertToRadians(55.0f), static_cast<float>(width_) / height_, 0.1f, 100.0f);
    XMStoreFloat4x4(&frame.viewProj, XMMatrixTranspose(view * proj));
    frame.lightDir = XMFLOAT4(0.4f, -1.0f, 0.3f, 0);
    float hurt = 1.0f - static_cast<float>(player.hp) / static_cast<float>(player.maxHp);
    frame.tint = XMFLOAT4(1, 1, 1, hurt);
    ctx->UpdateSubresource(frameConstants_.Get(), 0, nullptr, &frame, 0, 0);

    const float clear[4] = {0.05f, 0.06f, 0.09f, 1};
    ctx->ClearRenderTargetView(sceneTarget_.Get(), clear);
    ctx->ClearDepthStencilView(depth_.Get(), D3D11_CLEAR_DEPTH, 1.0f, 0);
    ctx->OMSetRenderTargets(1, sceneTarget_.GetAddressOf(), depth_.Get());
    D3D11_VIEWPORT viewport = {0, 0, static_cast<float>(width_), static_cast<float>(height_), 0, 1};
    ctx->RSSetViewports(1, &viewport);
    UINT stride = sizeof(Vertex), offset = 0;
    ctx->IASetVertexBuffers(0, 1, vertices_.GetAddressOf(), &stride, &offset);
    ctx->IASetIndexBuffer(indices_.Get(), DXGI_FORMAT_R16_UINT, 0);
    ctx->IASetInputLayout(layout_.Get());
    ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ctx->VSSetShader(sceneVS_.Get(), nullptr, 0);
    ctx->PSSetShader(scenePS_.Get(), nullptr, 0);
    ID3D11Buffer* constants[2] = {frameConstants_.Get(), objectConstants_.Get()};
    ctx->VSSetConstantBuffers(0, 2, constants);
    ctx->PSSetConstantBuffers(0, 2, constants);

    const float ground[4] = {0.25f, 0.3f, 0.25f, 1};
    DrawBox(ctx, 0, -0.55f, 0, 20, 0.1f, 20, ground);
    for (const auto& entity : world.Entities()) {
        float health = static_cast<float>(entity->hp) / static_cast<float>(entity->maxHp);
        float color[4] = {0.5f, 0.5f, 0.5f, 1};
        if (entity->Alive()) {
            if (entity->TypeId() == 1) {
                color[0] = 0.2f; color[1] = 0.4f + 0.5f * health; color[2] = 1.0f;
            } else {
                color[0] = 1.0f; color[1] = 0.25f; color[2] = 0.2f;
            }
        }
        float height = entity->TypeId() == 1 ? 1.6f : 1.0f;
        DrawBox(ctx, entity->pos.x, height * 0.5f - 0.5f, entity->pos.z, 1, height, 1, color);
    }

    // pass 2: post-process into the swap chain
    ID3D11ShaderResourceView* none[1] = {nullptr};
    ctx->OMSetRenderTargets(1, backBuffer_.GetAddressOf(), nullptr);
    ctx->IASetInputLayout(nullptr);
    ctx->VSSetShader(postVS_.Get(), nullptr, 0);
    ctx->PSSetShader(postPS_.Get(), nullptr, 0);
    ctx->PSSetShaderResources(0, 1, sceneView_.GetAddressOf());
    ctx->PSSetSamplers(0, 1, sampler_.GetAddressOf());
    ctx->Draw(3, 0);
    ctx->PSSetShaderResources(0, 1, none);
    swapChain_->Present(1, 0);
}

}  // namespace sg
