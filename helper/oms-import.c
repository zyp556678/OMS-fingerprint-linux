/*
 * oms-import — manage the mapping between on-chip OMS MOC templates and
 * fprintd storage.
 *
 * The OMS match-on-chip sensor stores templates in its own flash (slots 0..15).
 * fprintd only knows which template belongs to which user/finger via its file
 * storage (/var/lib/fprint/<user>/<driver>/<device-id>/<finger-hex>). Templates
 * enrolled under Windows (or through another OS/tool) have no such file, so
 * fprintd reports "no fingers enrolled". This tool manages those files.
 *
 * Modes:
 *   oms-import <username> --slot N=FINGER [--slot N=FINGER ...] [--dry-run]
 *       Adopt/assign chip template in slot N to the given finger. Any stale
 *       mapping of the same slot with a different finger is removed.
 *
 *   oms-import <username> --list
 *       Print a JSON description of all chip slots and their storage
 *       mappings (mappings need root; slots are readable as the user).
 *
 *   oms-import <username> --rename OLD=NEW
 *       Rename the finger mapping OLD to NEW (chip template untouched).
 *
 *   FINGER is one of: left-thumb left-index left-middle left-ring left-little
 *                     right-thumb right-index right-middle right-ring right-little
 *   (or a number 1..10)
 *
 * Run as root (writes /var/lib/fprint). Store root can be overridden with
 * the OMS_FPRINT_STORE environment variable (mainly for testing).
 *
 * Build:  cc -o oms-import oms-import.c $(pkg-config --cflags --libs libfprint-2 glib-2.0)
 */

#include <fprint.h>
#include <glib.h>
#include <glib/gstdio.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DEFAULT_STORE "/var/lib/fprint"
#define DIR_PERMS 0700
#define MAX_SLOT 15

static const char *finger_names[] = {
  "unknown",
  "left-thumb", "left-index", "left-middle", "left-ring", "left-little",
  "right-thumb", "right-index", "right-middle", "right-ring", "right-little",
};

static FpFinger
parse_finger (const char *s)
{
  for (int i = FP_FINGER_FIRST; i <= FP_FINGER_LAST; i++)
    {
      if (g_strcmp0 (s, finger_names[i]) == 0)
        return (FpFinger) i;
    }

  char *end = NULL;
  long v = strtol (s, &end, 10);
  if (end && *end == '\0' && v >= FP_FINGER_FIRST && v <= FP_FINGER_LAST)
    return (FpFinger) v;

  return FP_FINGER_UNKNOWN;
}

static gchar *
storage_device_dir (const char *store,
                    const char *username,
                    FpDevice   *device)
{
  return g_strdup_printf ("%s/%s/%s/%s", store, username,
                          fp_device_get_driver (device),
                          fp_device_get_device_id (device));
}

/* Parse "Slot N" from a print description. */
static gboolean
print_get_slot (FpPrint *print,
                guint   *slot)
{
  g_autofree gchar *description = NULL;
  unsigned s;

  g_object_get (print, "description", &description, NULL);
  if (!description || sscanf (description, "Slot %u", &s) != 1 || s > MAX_SLOT)
    return FALSE;

  *slot = s;
  return TRUE;
}

/* Deserialize a stored print file and return its chip slot. */
static gboolean
stored_file_get_slot (const char *path,
                      guint      *slot)
{
  g_autofree gchar *contents = NULL;
  g_autoptr(FpPrint) print = NULL;
  g_autoptr(GVariant) data = NULL;
  gsize len;
  guint16 s;

  if (!g_file_get_contents (path, &contents, &len, NULL))
    return FALSE;

  print = fp_print_deserialize ((guchar *) contents, len, NULL);
  if (!print)
    return FALSE;

  g_object_get (print, "fpi-data", &data, NULL);
  if (!data || !g_variant_is_of_type (data, G_VARIANT_TYPE ("(q)")))
    return FALSE;

  g_variant_get (data, "(q)", &s);
  if (s > MAX_SLOT)
    return FALSE;

  *slot = s;
  return TRUE;
}

/* slot -> finger code mapping from the storage directory (empty when the
 * directory cannot be read, e.g. when not running as root). */
static GHashTable *
scan_mappings (const char *dirpath)
{
  GHashTable *map = g_hash_table_new (g_direct_hash, g_direct_equal);
  g_autoptr(GError) err = NULL;
  GDir *dir = g_dir_open (dirpath, 0, &err);

  if (!dir)
    return map;

  for (const gchar *ent = g_dir_read_name (dir); ent;
       ent = g_dir_read_name (dir))
    {
      g_autofree gchar *path = NULL;
      char *end = NULL;
      long code;
      guint slot;

      if (strlen (ent) != 1)
        continue;

      code = strtol (ent, &end, 16);
      if (!end || *end || code < FP_FINGER_FIRST || code > FP_FINGER_LAST)
        continue;

      path = g_build_filename (dirpath, ent, NULL);
      if (!stored_file_get_slot (path, &slot))
        continue;

      g_hash_table_insert (map, GUINT_TO_POINTER (slot), GUINT_TO_POINTER (code));
    }

  return map;
}

static int
mode_list (FpDevice   *device,
           GPtrArray  *prints,
           const char *username,
           const char *store)
{
  g_autofree gchar *dirpath = storage_device_dir (store, username, device);
  g_autoptr(GHashTable) mappings = scan_mappings (dirpath);
  g_autoptr(GHashTable) on_chip = g_hash_table_new (g_direct_hash, g_direct_equal);
  gboolean sep = FALSE;

  for (guint i = 0; i < prints->len; i++)
    {
      guint slot;

      if (print_get_slot (g_ptr_array_index (prints, i), &slot))
        g_hash_table_add (on_chip, GUINT_TO_POINTER (slot));
    }

  g_print ("{\"driver\":\"%s\",\"device\":\"%s\",\"user\":\"%s\",\"slots\":[",
           fp_device_get_driver (device),
           fp_device_get_device_id (device),
           username);

  for (guint slot = 0; slot <= MAX_SLOT; slot++)
    {
      gpointer code = g_hash_table_lookup (mappings, GUINT_TO_POINTER (slot));
      gboolean chip = g_hash_table_contains (on_chip, GUINT_TO_POINTER (slot));

      if (!chip && !code)
        continue;

      g_print ("%s{\"slot\":%u,\"on_chip\":%s,\"finger_code\":",
               sep ? "," : "", slot, chip ? "true" : "false");
      if (code)
        g_print ("%u", GPOINTER_TO_UINT (code));
      else
        g_print ("null");
      g_print ("}");
      sep = TRUE;
    }

  g_print ("]}\n");
  return EXIT_SUCCESS;
}

static int
mode_rename (FpDevice   *device,
             const char *username,
             const char *store,
             FpFinger    old_finger,
             FpFinger    new_finger)
{
  g_autofree gchar *dirpath = storage_device_dir (store, username, device);
  g_autofree gchar *old_path = g_strdup_printf ("%s/%x", dirpath, old_finger);
  g_autofree gchar *new_path = g_strdup_printf ("%s/%x", dirpath, new_finger);
  g_autofree gchar *contents = NULL;
  g_autofree gchar *buf = NULL;
  g_autoptr(FpPrint) print = NULL;
  gsize len = 0, out_len = 0;

  if (old_finger == new_finger)
    {
      g_printerr ("Old and new finger are the same.\n");
      return EXIT_FAILURE;
    }

  if (!g_file_get_contents (old_path, &contents, &len, NULL))
    {
      g_printerr ("No mapping found for %s (%s).\n",
                  finger_names[old_finger], old_path);
      return EXIT_FAILURE;
    }

  if (g_file_test (new_path, G_FILE_TEST_EXISTS))
    {
      g_printerr ("Target mapping %s already exists (%s).\n",
                  finger_names[new_finger], new_path);
      return EXIT_FAILURE;
    }

  print = fp_print_deserialize ((guchar *) contents, len, NULL);
  if (!print || fp_print_get_finger (print) != old_finger)
    {
      g_printerr ("Stored mapping for %s is invalid.\n", finger_names[old_finger]);
      return EXIT_FAILURE;
    }

  fp_print_set_finger (print, new_finger);

  if (!fp_print_serialize (print, (guchar **) &buf, &out_len, NULL))
    {
      g_printerr ("Failed to serialize the print.\n");
      return EXIT_FAILURE;
    }

  if (g_file_set_contents (new_path, buf, out_len, NULL) == FALSE)
    {
      g_printerr ("Failed to write %s: %s\n", new_path, g_strerror (errno));
      return EXIT_FAILURE;
    }

  if (g_unlink (old_path) != 0)
    {
      g_printerr ("Warning: failed to remove old mapping %s: %s\n",
                  old_path, g_strerror (errno));
    }

  g_print ("Renamed %s -> %s\n",
           finger_names[old_finger], finger_names[new_finger]);
  return EXIT_SUCCESS;
}

static void
cleanup_slot_stale (const char *dirpath,
                    guint       slot,
                    FpFinger    wanted)
{
  g_autoptr(GError) err = NULL;
  GDir *dir = g_dir_open (dirpath, 0, &err);

  if (!dir)
    return;

  for (const gchar *ent = g_dir_read_name (dir); ent;
       ent = g_dir_read_name (dir))
    {
      g_autofree gchar *path = NULL;
      char *end = NULL;
      long code;
      guint file_slot;

      if (strlen (ent) != 1)
        continue;

      code = strtol (ent, &end, 16);
      if (!end || *end || code < FP_FINGER_FIRST || code > FP_FINGER_LAST ||
          code == (long) wanted)
        continue;

      path = g_build_filename (dirpath, ent, NULL);
      if (!stored_file_get_slot (path, &file_slot) || file_slot != slot)
        continue;

      if (g_unlink (path) == 0)
        g_print ("Removed stale mapping %s for slot %u\n", path, slot);
    }

  g_dir_close (dir);
}

int
main (int argc, char **argv)
{
  g_autoptr(FpContext) ctx = NULL;
  g_autoptr(GPtrArray) prints = NULL;
  g_autoptr(GError) error = NULL;
  const char *username = NULL;
  const char *store = NULL;
  FpDevice *device = NULL;
  FpFinger slot_finger[MAX_SLOT + 1] = { FP_FINGER_UNKNOWN };
  FpFinger rename_old = FP_FINGER_UNKNOWN;
  FpFinger rename_new = FP_FINGER_UNKNOWN;
  gboolean dry_run = FALSE;
  gboolean list_mode = FALSE;
  gboolean rename_mode = FALSE;
  GPtrArray *devices;
  int adopted = 0;

  if (argc < 2)
    {
      g_printerr ("Usage:\n"
                  "  %s <username> --slot N=FINGER [...] [--dry-run]\n"
                  "  %s <username> --list\n"
                  "  %s <username> --rename OLD=NEW\n",
                  argv[0], argv[0], argv[0]);
      return EXIT_FAILURE;
    }

  username = argv[1];

  for (int i = 2; i < argc; i++)
    {
      if (g_strcmp0 (argv[i], "--dry-run") == 0)
        {
          dry_run = TRUE;
        }
      else if (g_strcmp0 (argv[i], "--list") == 0)
        {
          list_mode = TRUE;
        }
      else if (g_strcmp0 (argv[i], "--rename") == 0 && i + 1 < argc)
        {
          char old_name[64], new_name[64];

          i++;
          if (sscanf (argv[i], "%63[^=]=%63s", old_name, new_name) != 2)
            {
              g_printerr ("Bad --rename argument: %s (expected OLD=NEW)\n", argv[i]);
              return EXIT_FAILURE;
            }

          rename_old = parse_finger (old_name);
          rename_new = parse_finger (new_name);
          if (rename_old == FP_FINGER_UNKNOWN || rename_new == FP_FINGER_UNKNOWN)
            {
              g_printerr ("Unknown finger in --rename %s\n", argv[i]);
              return EXIT_FAILURE;
            }
          rename_mode = TRUE;
        }
      else if (g_str_has_prefix (argv[i], "--slot") && i + 1 < argc)
        {
          unsigned slot;
          char finger[64];
          FpFinger parsed;

          i++;
          if (sscanf (argv[i], "%u=%63s", &slot, finger) != 2 || slot > MAX_SLOT)
            {
              g_printerr ("Bad --slot argument: %s (expected N=FINGER, N=0..%u)\n",
                          argv[i], MAX_SLOT);
              return EXIT_FAILURE;
            }

          parsed = parse_finger (finger);
          if (parsed == FP_FINGER_UNKNOWN)
            {
              g_printerr ("Unknown finger: %s\n", finger);
              return EXIT_FAILURE;
            }
          slot_finger[slot] = parsed;
        }
      else
        {
          g_printerr ("Unrecognized argument: %s\n", argv[i]);
          return EXIT_FAILURE;
        }
    }

  store = g_getenv ("OMS_FPRINT_STORE");
  if (!store || !*store)
    store = DEFAULT_STORE;

  ctx = fp_context_new ();
  devices = fp_context_get_devices (ctx);

  for (guint i = 0; devices && i < devices->len; i++)
    {
      FpDevice *candidate = g_ptr_array_index (devices, i);

      if (g_strcmp0 (fp_device_get_driver (candidate), "omsmoc") == 0)
        {
          device = candidate;
          break;
        }
    }

  if (!device)
    {
      g_printerr ("No omsmoc device found.\n");
      return EXIT_FAILURE;
    }

  if (!fp_device_open_sync (device, NULL, &error))
    {
      g_printerr ("Failed to open device: %s\n", error->message);
      return EXIT_FAILURE;
    }

  if (rename_mode)
    {
      int ret = dry_run
                ? (g_print ("dry-run: would rename %s -> %s\n",
                            finger_names[rename_old], finger_names[rename_new]),
                   EXIT_SUCCESS)
                : mode_rename (device, username, store, rename_old, rename_new);

      fp_device_close_sync (device, NULL, NULL);
      return ret;
    }

  prints = fp_device_list_prints_sync (device, NULL, &error);
  if (!prints)
    {
      g_printerr ("Failed to list prints: %s\n", error->message);
      fp_device_close_sync (device, NULL, NULL);
      return EXIT_FAILURE;
    }

  if (list_mode)
    {
      int ret = mode_list (device, prints, username, store);

      fp_device_close_sync (device, NULL, NULL);
      return ret;
    }

  /* ------------------------------------------------------------- import */

  for (int s = 0; s <= MAX_SLOT; s++)
    {
      for (int t = s + 1; t <= MAX_SLOT; t++)
        {
          if (slot_finger[s] != FP_FINGER_UNKNOWN &&
              slot_finger[s] == slot_finger[t])
            {
              g_printerr ("Finger %s given for two slots (%d and %d).\n",
                          finger_names[slot_finger[s]], s, t);
              fp_device_close_sync (device, NULL, NULL);
              return EXIT_FAILURE;
            }
        }
    }

  if (prints->len == 0)
    {
      g_printerr ("The chip has no templates.\n");
      fp_device_close_sync (device, NULL, NULL);
      return EXIT_FAILURE;
    }

  for (guint i = 0; i < prints->len; i++)
    {
      FpPrint *print = g_ptr_array_index (prints, i);
      g_autofree gchar *path = NULL;
      g_autofree gchar *dirpath = NULL;
      g_autofree gchar *buf = NULL;
      gsize len = 0;
      guint slot;
      FpFinger finger;

      if (!print_get_slot (print, &slot))
        continue;

      finger = slot_finger[slot];
      if (finger == FP_FINGER_UNKNOWN)
        {
          g_print ("Skipping chip slot %u (no mapping given)\n", slot);
          continue;
        }

      fp_print_set_finger (print, finger);
      fp_print_set_username (print, username);

      if (!fp_print_serialize (print, (guchar **) &buf, &len, &error))
        {
          g_printerr ("Failed to serialize slot %u: %s\n", slot, error->message);
          fp_device_close_sync (device, NULL, NULL);
          return EXIT_FAILURE;
        }

      path = g_strdup_printf ("%s/%s/%s/%s/%x",
                              store, username,
                              fp_print_get_driver (print),
                              fp_print_get_device_id (print),
                              finger);
      dirpath = g_path_get_dirname (path);

      g_print ("Chip slot %u -> %s (%s, %zu bytes) %s\n",
               slot, path, finger_names[finger], len,
               dry_run ? "[dry-run]" : "");

      if (!dry_run)
        {
          if (g_mkdir_with_parents (dirpath, DIR_PERMS) != 0)
            {
              g_printerr ("Failed to create %s: %s\n", dirpath, g_strerror (errno));
              fp_device_close_sync (device, NULL, NULL);
              return EXIT_FAILURE;
            }
          if (!g_file_set_contents (path, buf, len, &error))
            {
              g_printerr ("Failed to write %s: %s\n", path, error->message);
              fp_device_close_sync (device, NULL, NULL);
              return EXIT_FAILURE;
            }

          cleanup_slot_stale (dirpath, slot, finger);
        }

      adopted++;
    }

  fp_device_close_sync (device, NULL, NULL);

  g_print ("%s: %d template(s) %s\n", dry_run ? "dry-run" : "done",
           adopted, dry_run ? "would be imported" : "imported into fprintd storage");

  return adopted > 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
