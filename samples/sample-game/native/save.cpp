#include "save.h"

#include <cstring>
#include <fstream>
#include <iterator>
#include <vector>

namespace sg {

namespace {

constexpr char kMagic[4] = {'S', 'G', 'S', 'V'};
constexpr uint16_t kVersion = 2;

class Writer {
public:
    template <typename T>
    void Put(const T& value) {
        const auto* p = reinterpret_cast<const uint8_t*>(&value);
        bytes.insert(bytes.end(), p, p + sizeof(T));
    }
    void PutBytes(const void* data, size_t size) {
        const auto* p = static_cast<const uint8_t*>(data);
        bytes.insert(bytes.end(), p, p + size);
    }
    std::vector<uint8_t> bytes;
};

class Reader {
public:
    explicit Reader(const std::vector<uint8_t>& data) : bytes(data) {}
    template <typename T>
    bool Get(T& value) {
        if (offset + sizeof(T) > bytes.size()) {
            return false;
        }
        std::memcpy(&value, bytes.data() + offset, sizeof(T));
        offset += sizeof(T);
        return true;
    }
    bool GetString(std::string& out, size_t size) {
        if (offset + size > bytes.size()) {
            return false;
        }
        out.assign(reinterpret_cast<const char*>(bytes.data() + offset), size);
        offset += size;
        return true;
    }
    const std::vector<uint8_t>& bytes;
    size_t offset = 0;
};

}  // namespace

uint32_t Crc32(const uint8_t* data, size_t size) {
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < size; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
        }
    }
    return ~crc;
}

bool SaveWorld(const World& world, const std::wstring& path, uint64_t timestamp) {
    Writer w;
    w.PutBytes(kMagic, sizeof(kMagic));
    w.Put<uint16_t>(kVersion);
    w.Put<uint16_t>(0);  // flags
    w.Put<uint32_t>(static_cast<uint32_t>(world.Entities().size()));
    w.Put<uint64_t>(timestamp);
    w.Put<uint32_t>(world.tick);
    for (const auto& entity : world.Entities()) {
        w.Put<uint8_t>(entity->TypeId());
        auto length = static_cast<uint8_t>(entity->name.size() > 255 ? 255 : entity->name.size());
        w.Put<uint8_t>(length);
        w.PutBytes(entity->name.data(), length);
        w.Put<float>(entity->pos.x);
        w.Put<float>(entity->pos.y);
        w.Put<float>(entity->pos.z);
        w.Put<int32_t>(entity->hp);
        w.Put<int32_t>(entity->maxHp);
        if (entity->TypeId() == 1) {
            const auto& player = static_cast<const Player&>(*entity);
            w.Put<uint32_t>(player.score);
            w.Put<float>(player.regenTimer);
        } else {
            const auto& enemy = static_cast<const Enemy&>(*entity);
            w.Put<uint32_t>(static_cast<uint32_t>(enemy.damage));
            w.Put<float>(enemy.attackTimer);
        }
    }
    w.Put<uint32_t>(Crc32(w.bytes.data(), w.bytes.size()));
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out.write(reinterpret_cast<const char*>(w.bytes.data()), static_cast<std::streamsize>(w.bytes.size()));
    return static_cast<bool>(out);
}

bool LoadWorld(World& world, const std::wstring& path) {
    std::ifstream in(path, std::ios::binary);
    std::vector<uint8_t> data((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    if (data.size() < 28 || std::memcmp(data.data(), kMagic, sizeof(kMagic)) != 0) {
        return false;
    }
    uint32_t stored = 0;
    std::memcpy(&stored, data.data() + data.size() - 4, 4);
    if (stored != Crc32(data.data(), data.size() - 4)) {
        return false;  // corrupted or edited by hand
    }
    Reader r(data);
    r.offset = 4;
    uint16_t version = 0, flags = 0;
    uint32_t count = 0, tick = 0;
    uint64_t timestamp = 0;
    if (!r.Get(version) || version != kVersion || !r.Get(flags) || !r.Get(count) || !r.Get(timestamp) || !r.Get(tick)) {
        return false;
    }
    std::vector<std::unique_ptr<Entity>> loaded;
    for (uint32_t i = 0; i < count; i++) {
        uint8_t type = 0, length = 0;
        std::string name;
        Vec3 pos;
        int32_t hp = 0, maxHp = 0;
        uint32_t extra = 0;
        float timer = 0;
        if (!r.Get(type) || !r.Get(length) || !r.GetString(name, length) || !r.Get(pos.x) || !r.Get(pos.y) ||
            !r.Get(pos.z) || !r.Get(hp) || !r.Get(maxHp) || !r.Get(extra) || !r.Get(timer)) {
            return false;
        }
        if (type == 1) {
            auto player = std::make_unique<Player>(name);
            player->score = extra;
            player->regenTimer = timer;
            loaded.push_back(std::move(player));
        } else {
            auto enemy = std::make_unique<Enemy>(name, pos, static_cast<int32_t>(extra));
            enemy->attackTimer = timer;
            loaded.push_back(std::move(enemy));
        }
        loaded.back()->pos = pos;
        loaded.back()->hp = hp;
        loaded.back()->maxHp = maxHp;
    }
    return world.Adopt(std::move(loaded), tick);
}

}  // namespace sg
