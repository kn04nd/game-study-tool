// Sample game for the Game Study Toolkit: a tiny arena where three enemies chase the player.
// Written to be studied: RTTI classes, a known HP field, a binary save format and a two-pass D3D11 frame.
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace sg {

struct Vec3 {
    float x = 0, y = 0, z = 0;
};

class World;

// Every living thing in the arena. `hp` is the value the runtime exercises look for in memory.
class Entity {
public:
    Entity(std::string name, Vec3 pos, int32_t maxHp);
    virtual ~Entity() = default;
    virtual const char* Kind() const = 0;
    virtual uint8_t TypeId() const = 0;
    virtual void Update(World& world, float dt) = 0;
    void TakeDamage(int32_t amount);
    bool Alive() const { return hp > 0; }

    std::string name;
    Vec3 pos;
    int32_t hp;
    int32_t maxHp;
};

class Player final : public Entity {
public:
    explicit Player(std::string name);
    const char* Kind() const override { return "Player"; }
    uint8_t TypeId() const override { return 1; }
    void Update(World& world, float dt) override;
    void Heal(int32_t amount);

    uint32_t score = 0;
    float regenTimer = 0;
};

class Enemy final : public Entity {
public:
    Enemy(std::string name, Vec3 pos, int32_t damage);
    const char* Kind() const override { return "Enemy"; }
    uint8_t TypeId() const override { return 2; }
    void Update(World& world, float dt) override;

    int32_t damage;
    float attackTimer = 0;
};

struct Settings {
    float difficulty = 1.0f;      // multiplies enemy damage
    float attackInterval = 2.0f;  // seconds between enemy hits
};

class World {
public:
    explicit World(Settings settings);
    void Reset();
    void Step(float dt);
    Player& PlayerRef() { return *player; }
    const Player& PlayerRef() const { return *player; }
    const std::vector<std::unique_ptr<Entity>>& Entities() const { return entities; }
    bool Adopt(std::vector<std::unique_ptr<Entity>> loaded, uint32_t loadedTick);

    Settings settings;
    uint32_t tick = 0;

private:
    std::vector<std::unique_ptr<Entity>> entities;
    Player* player = nullptr;
};

// settings.ini next to the executable: `difficulty=1.5`, `attack_interval=1.0`
Settings LoadSettings(const std::wstring& path);

}  // namespace sg
