using System.Text;

namespace SampleGame.SaveTool;

public enum EntityType : byte
{
    Player = 1,
    Enemy = 2,
}

public sealed class EntityRecord
{
    public EntityType Type { get; set; }
    public string Name { get; set; } = "";
    public float X { get; set; }
    public float Y { get; set; }
    public float Z { get; set; }
    public int Hp { get; set; }
    public int MaxHp { get; set; }

    /// <summary>Player: score. Enemy: damage per hit.</summary>
    public uint Extra { get; set; }

    /// <summary>Player: regeneration timer. Enemy: attack timer (seconds).</summary>
    public float Timer { get; set; }
}

public sealed class SaveFile
{
    public const ushort CurrentVersion = 2;
    public static readonly byte[] Magic = "SGSV"u8.ToArray();

    public ushort Version { get; set; } = CurrentVersion;
    public ushort Flags { get; set; }
    public ulong Timestamp { get; set; }
    public uint Tick { get; set; }
    public List<EntityRecord> Entities { get; } = new();
    public uint StoredCrc { get; private set; }

    public static SaveFile Read(byte[] data)
    {
        if (data.Length < 28 || !data.AsSpan(0, 4).SequenceEqual(Magic))
        {
            throw new InvalidDataException("not an SGSV save");
        }
        using var reader = new BinaryReader(new MemoryStream(data), Encoding.UTF8);
        reader.ReadBytes(4);
        var save = new SaveFile { Version = reader.ReadUInt16(), Flags = reader.ReadUInt16() };
        uint count = reader.ReadUInt32();
        save.Timestamp = reader.ReadUInt64();
        save.Tick = reader.ReadUInt32();
        for (uint i = 0; i < count; i++)
        {
            var entity = new EntityRecord { Type = (EntityType)reader.ReadByte() };
            entity.Name = Encoding.UTF8.GetString(reader.ReadBytes(reader.ReadByte()));
            entity.X = reader.ReadSingle();
            entity.Y = reader.ReadSingle();
            entity.Z = reader.ReadSingle();
            entity.Hp = reader.ReadInt32();
            entity.MaxHp = reader.ReadInt32();
            entity.Extra = reader.ReadUInt32();
            entity.Timer = reader.ReadSingle();
            save.Entities.Add(entity);
        }
        save.StoredCrc = reader.ReadUInt32();
        return save;
    }

    public byte[] Write()
    {
        using var stream = new MemoryStream();
        using (var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: true))
        {
            writer.Write(Magic);
            writer.Write(Version);
            writer.Write(Flags);
            writer.Write((uint)Entities.Count);
            writer.Write(Timestamp);
            writer.Write(Tick);
            foreach (var entity in Entities)
            {
                byte[] name = Encoding.UTF8.GetBytes(entity.Name);
                writer.Write((byte)entity.Type);
                writer.Write((byte)Math.Min(name.Length, 255));
                writer.Write(name, 0, Math.Min(name.Length, 255));
                writer.Write(entity.X);
                writer.Write(entity.Y);
                writer.Write(entity.Z);
                writer.Write(entity.Hp);
                writer.Write(entity.MaxHp);
                writer.Write(entity.Extra);
                writer.Write(entity.Timer);
            }
        }
        byte[] body = stream.ToArray();
        byte[] result = new byte[body.Length + 4];
        body.CopyTo(result, 0);
        BitConverter.GetBytes(Crc32(body)).CopyTo(result, body.Length);
        return result;
    }

    public static uint Crc32(ReadOnlySpan<byte> data)
    {
        uint crc = 0xFFFFFFFFu;
        foreach (byte b in data)
        {
            crc ^= b;
            for (int bit = 0; bit < 8; bit++)
            {
                crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
            }
        }
        return ~crc;
    }
}
