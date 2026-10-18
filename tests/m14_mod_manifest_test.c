/* Host test for mod info files (include/openspm/mod_manifest.h): a full
   file, comments and unknown keys, and each kind of mistake refused with the
   line it is on. */
#include "openspm/mod_manifest.h"

#include <assert.h>
#include <stdlib.h>

int main(void) {
    openspm_mod_manifest m;
    char why[160];
    static const char good[] =
        "# Francis\n"
        "id = \"openspm.sample.francis\"\n"
        "name = \"Francis\"   # shown in F1\n"
        "version = \"1.0.0\"\r\n"
        "author = \"OpenSPM\"\n"
        "description = \"Big, slow, a \\\"tongue\\\".\"\n"
        "requires = [\"openspm.content>=1\", \"openspm.overlay>=2\"]\n"
        "future_key = 3\n"
        "\n";
    assert(openspm_manifest_parse(good, &m, why, sizeof why));
    assert(strcmp(m.id, "openspm.sample.francis") == 0 && strcmp(m.name, "Francis") == 0);
    assert(strcmp(m.version, "1.0.0") == 0 && strcmp(m.author, "OpenSPM") == 0);
    assert(strcmp(m.description, "Big, slow, a \"tongue\".") == 0);
    assert(m.require_count == 2u && strcmp(m.needs[0].service, "openspm.content") == 0 &&
           m.needs[0].min_version == 1u && m.needs[1].min_version == 2u);
    /* Just an id is enough. */
    assert(openspm_manifest_parse("id = \"a.b\"", &m, why, sizeof why) && m.require_count == 0u);
    {
        static const char *const bad[] = {
            "name = \"x\"\n",                                  /* no id */
            "id = \"Bad Id\"\n",                                /* id charset */
            "id = \"a.b\"\nname = \"unterminated\n",            /* string */
            "id = \"a.b\"\nrequires = [\"openspm.content\"]\n", /* no >= */
            "id = \"a.b\"\nrequires = [\"openspm.content>=0\"]\n",
            "id = \"a.b\"\nrequires = \"openspm.content>=1\"\n", /* not an array */
            "id = \"a.b\" extra\n",
            "id \"a.b\"\n",
        };
        size_t i;
        for (i = 0; i < sizeof bad / sizeof bad[0]; ++i) {
            why[0] = 0;
            assert(!openspm_manifest_parse(bad[i], &m, why, sizeof why) && why[0] != 0);
        }
        assert(!openspm_manifest_parse("id = \"a.b\"\nname = \"unterminated\n", &m, why, sizeof why) &&
               strstr(why, "line 2") != NULL);
    }
#ifdef OPENSPM_SOURCE_DIR
    {
        /* Every sample's info file parses and names the DLL's own mod_id. */
        static const char *const mods[] = {"debug", "shell_crouch", "striker", "glock", "sfx_log", "hitbox_view", "francis"};
        size_t i;
        for (i = 0; i < sizeof mods / sizeof mods[0]; ++i) {
            char path[512], text[16384], want[96];
            FILE *f;
            size_t n;
            snprintf(path, sizeof path, "%s/mods/%s/openspm_%s.toml", OPENSPM_SOURCE_DIR, mods[i], mods[i]);
            f = fopen(path, "rb");
            assert(f != NULL);
            n = fread(text, 1, sizeof text - 1u, f);
            fclose(f);
            text[n] = 0;
            if (!openspm_manifest_parse(text, &m, why, sizeof why)) {
                printf("%s: %s\n", path, why);
                assert(0);
            }
            snprintf(path, sizeof path, "%s/mods/%s/openspm_%s.c", OPENSPM_SOURCE_DIR, mods[i], mods[i]);
            f = fopen(path, "rb");
            assert(f != NULL);
            {
                char *source = (char *)calloc(1, 1u << 20);
                assert(source != NULL);
                n = fread(source, 1, (1u << 20) - 1u, f);
                fclose(f);
                snprintf(want, sizeof want, "mod_id = \"%s\";", m.id);
                if (strstr(source, want) == NULL) {
                    printf("%s: no %s\n", path, want);
                    assert(0);
                }
                free(source);
            }
        }
    }
#endif
    puts("PASS: mod manifest");
    return 0;
}
