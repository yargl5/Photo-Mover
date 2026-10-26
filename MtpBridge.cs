using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Security.Cryptography;
using System.Web.Script.Serialization;
using System.Collections.Generic;
using System.Diagnostics;
using MediaDevices;

// Small JSON-over-stdio bridge. Source deletion belongs exclusively to the UI worker.
class MtpBridge
{
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    [STAThread]
    static int Main()
    {
        Console.InputEncoding = Encoding.UTF8;
        Console.OutputEncoding = new UTF8Encoding(false);
        var devices = MediaDevice.GetDevices().ToList();
        var directoryIndexes = new Dictionary<string, Dictionary<string, MediaFileSystemInfo>>(StringComparer.Ordinal);
        var parentIds = new Dictionary<string, string>(StringComparer.Ordinal);
        try
        {
            string line;
            while ((line = Console.In.ReadLine()) != null)
            {
              try
              {
                var request = Json.Deserialize<Dictionary<string, string>>(line);
                string command = request["command"];
                if (command == "devices")
                    Reply(new { ok = true, devices = devices.Select(d => new { id = d.DeviceId, name = d.FriendlyName ?? d.Description ?? "Телефон" }).ToArray() });
                else
                {
                    var device = devices.SingleOrDefault(d => d.DeviceId == request["device"]);
                    if (device == null) throw new IOException("Телефон отключён. Подключите его заново.");
                    if (!device.IsConnected) device.Connect(enableCache: false);
                    if (command == "directories")
                        Reply(new { ok = true, directories = device.EnumerateDirectories(request["path"]).ToArray() });
                    else if (command == "upload")
                    {
                        string target = request["path"];
                        string parent = Path.GetDirectoryName(target);
                        var lookupTimer = Stopwatch.StartNew();
                        string directoryKey = device.DeviceId + "\n" + parent;
                        Dictionary<string, MediaFileSystemInfo> index;
                        if (!directoryIndexes.TryGetValue(directoryKey, out index))
                        {
                            if (!device.DirectoryExists(parent)) device.CreateDirectory(parent);
                            index = new Dictionary<string, MediaFileSystemInfo>(StringComparer.OrdinalIgnoreCase);
                            var directory = device.GetDirectoryInfo(parent);
                            parentIds[directoryKey] = directory.Id;
                            foreach (var entry in directory.EnumerateFileSystemInfos())
                            {
                                // Ambiguous names must never cause source deletion.
                                if (index.ContainsKey(entry.Name)) index[entry.Name] = null;
                                else index.Add(entry.Name, entry);
                            }
                            directoryIndexes.Add(directoryKey, index);
                        }
                        MediaFileSystemInfo existing;
                        string name = Path.GetFileName(target);
                        bool alreadyPresent = index.TryGetValue(name, out existing);
                        var file = existing as MediaFileInfo;
                        if (alreadyPresent && file == null)
                            throw new IOException("Имя занято папкой или неоднозначно. Оригинал сохранён.");
                        if (alreadyPresent) file.Refresh();
                        double lookupSeconds = lookupTimer.Elapsed.TotalSeconds;
                        string expected;
                        long size;
                        double uploadSeconds, verifySeconds, sourceHashSeconds;
                        using (var source = new FileStream(request["source"], FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024, FileOptions.SequentialScan))
                        {
                            size = source.Length;
                            if (alreadyPresent && file.Length != (ulong)size)
                                throw new IOException("Имя совпадает, но размер копии другой. Оригинал сохранён.");
                            var hashTimer = Stopwatch.StartNew();
                            using (var sha = SHA256.Create()) expected = Hex(sha.ComputeHash(source));
                            sourceHashSeconds = hashTimer.Elapsed.TotalSeconds;
                            source.Position = 0;
                            var timer = Stopwatch.StartNew();
                            if (!alreadyPresent)
                            {
                                // Create a new WPD object using the cached parent ID, then retain
                                // the returned ID. Never reopen an existing file for writing.
                                file = WpdUpload.Create(device, parentIds[directoryKey], name, source);
                                if (!String.Equals(file.Name, name, StringComparison.OrdinalIgnoreCase))
                                    throw new IOException("Телефон изменил имя файла. Оригинал сохранён.");
                                index[name] = file;
                            }
                            uploadSeconds = alreadyPresent ? 0 : timer.Elapsed.TotalSeconds;
                            timer.Restart();
                            using (var sha = SHA256.Create())
                            using (var sink = new CryptoStream(Stream.Null, sha, CryptoStreamMode.Write))
                            {
                                // Read bytes back through WPD, without keeping another disk copy.
                                // Open the WPD object already resolved above. DownloadFile(path)
                                // would enumerate the large directory yet again.
                                using (var remote = file.OpenRead()) remote.CopyTo(sink);
                                sink.FlushFinalBlock();
                                if (Hex(sha.Hash) != expected) throw new IOException("Проверка SHA-256 копии на телефоне не пройдена. Оригинал сохранён.");
                            }
                            verifySeconds = timer.Elapsed.TotalSeconds;
                        }
                        Reply(new { ok = true, sha256 = expected, size = size, already_present = alreadyPresent, upload_seconds = uploadSeconds, verify_seconds = verifySeconds, lookup_seconds = lookupSeconds, source_hash_seconds = sourceHashSeconds });
                    }
                    else throw new ArgumentException("Unknown command");
                }
              }
              catch (Exception error)
              {
                  string message = error.HResult == unchecked((int)0x800700AA)
                      ? "Телефон занят другой операцией. Дождитесь её завершения и повторите перенос. Оригинал сохранён."
                      : error.Message;
                  Reply(new { ok = false, error = message });
              }
            }
            return 0;
        }
        finally { foreach (var d in devices) d.Dispose(); }
    }
    static string Hex(byte[] hash) { return BitConverter.ToString(hash).Replace("-", "").ToLowerInvariant(); }
    static void Reply(object value) { Console.WriteLine(Json.Serialize(value)); Console.Out.Flush(); }
}
