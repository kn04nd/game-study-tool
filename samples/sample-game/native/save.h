// SGSV save files: little-endian header, one record per entity, CRC-32 of everything before it.
#pragma once

#include <string>

#include "game.h"

namespace sg {

bool SaveWorld(const World& world, const std::wstring& path, uint64_t timestamp);
bool LoadWorld(World& world, const std::wstring& path);
uint32_t Crc32(const uint8_t* data, size_t size);

}  // namespace sg
