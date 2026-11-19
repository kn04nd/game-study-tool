using System.Text.Json;
using SampleGame.SaveTool;

// SampleGame.SaveTool dump <save.sgs>    the save as JSON
// SampleGame.SaveTool verify <save.sgs>  CRC check and a byte-exact rewrite check
if (args.Length != 2 || args[0] is not ("dump" or "verify"))
{
    Console.Error.WriteLine("usage: SampleGame.SaveTool dump|verify <save.sgs>");
    return 2;
}

byte[] data = File.ReadAllBytes(args[1]);
SaveFile save = SaveFile.Read(data);
if (args[0] == "dump")
{
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        save.Version,
        save.Flags,
        save.Timestamp,
        save.Tick,
        Entities = save.Entities.Select(e => new { Type = e.Type.ToString(), e.Name, e.X, e.Y, e.Z, e.Hp, e.MaxHp, e.Extra, e.Timer }),
    }, new JsonSerializerOptions { WriteIndented = true }));
    return 0;
}

uint computed = SaveFile.Crc32(data.AsSpan(0, data.Length - 4));
bool crcOk = computed == save.StoredCrc;
bool roundTrip = save.Write().AsSpan().SequenceEqual(data);
Console.WriteLine($"crc {(crcOk ? "ok" : "MISMATCH")} (stored {save.StoredCrc:x8}, computed {computed:x8}); rewrite {(roundTrip ? "identical" : "DIFFERENT")}");
return crcOk && roundTrip ? 0 : 1;
