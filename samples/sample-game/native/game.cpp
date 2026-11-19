#include "game.h"

#include <algorithm>
#include <cmath>
#include <fstream>

namespace sg {

Entity::Entity(std::string n, Vec3 p, int32_t m) : name(std::move(n)), pos(p), hp(m), maxHp(m) {}

void Entity::TakeDamage(int32_t amount) {
    hp = std::max<int32_t>(0, hp - amount);  // the write "who changes my HP?" leads to
}

Player::Player(std::string n) : Entity(std::move(n), {0, 0, 0}, 100) {}

void Player::Heal(int32_t amount) {
    if (Alive()) {
        hp = std::min(maxHp, hp + amount);
    }
}

void Player::Update(World&, float dt) {
    regenTimer += dt;
    if (regenTimer >= 5.0f) {
        regenTimer = 0;
        Heal(1);
    }
}

Enemy::Enemy(std::string n, Vec3 p, int32_t d) : Entity(std::move(n), p, 30), damage(d) {}

void Enemy::Update(World& world, float dt) {
    Player& player = world.PlayerRef();
    if (!Alive() || !player.Alive()) {
        return;
    }
    float dx = player.pos.x - pos.x, dz = player.pos.z - pos.z;
    float dist = std::sqrt(dx * dx + dz * dz);
    if (dist > 1.5f) {
        pos.x += dx / dist * 1.2f * dt;
        pos.z += dz / dist * 1.2f * dt;
        return;
    }
    attackTimer += dt;
    if (attackTimer >= world.settings.attackInterval) {
        attackTimer = 0;
        player.TakeDamage(static_cast<int32_t>(static_cast<float>(damage) * world.settings.difficulty));
    }
}

World::World(Settings s) : settings(s) { Reset(); }

void World::Reset() {
    entities.clear();
    auto hero = std::make_unique<Player>("Hero");
    player = hero.get();
    entities.push_back(std::move(hero));
    const char* names[] = {"Slime", "Bat", "Golem"};
    for (int i = 0; i < 3; i++) {
        float angle = static_cast<float>(i) * 2.0944f;
        entities.push_back(std::make_unique<Enemy>(names[i], Vec3{std::cos(angle) * 6.0f, 0.0f, std::sin(angle) * 6.0f},
                                                   3 + i * 2));
    }
    tick = 0;
}

void World::Step(float dt) {
    tick++;
    for (auto& entity : entities) {
        entity->Update(*this, dt);
    }
    if (tick % 60 == 0 && player->Alive()) {
        player->score++;  // one point per second alive
    }
}

bool World::Adopt(std::vector<std::unique_ptr<Entity>> loaded, uint32_t loadedTick) {
    Player* found = nullptr;
    for (auto& entity : loaded) {
        if (entity->TypeId() == 1) {
            found = static_cast<Player*>(entity.get());
        }
    }
    if (found == nullptr) {
        return false;
    }
    entities = std::move(loaded);
    player = found;
    tick = loadedTick;
    return true;
}

Settings LoadSettings(const std::wstring& path) {
    Settings s;
    std::ifstream in(path);
    std::string line;
    while (std::getline(in, line)) {
        auto eq = line.find('=');
        if (eq == std::string::npos) {
            continue;
        }
        std::string key = line.substr(0, eq), value = line.substr(eq + 1);
        try {
            if (key == "difficulty") {
                s.difficulty = std::stof(value);
            } else if (key == "attack_interval") {
                s.attackInterval = std::stof(value);
            }
        } catch (...) {
            // a bad value keeps the default
        }
    }
    return s;
}

}  // namespace sg
