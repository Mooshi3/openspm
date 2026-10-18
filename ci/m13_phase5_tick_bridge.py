from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text().replace("\r\n", "\n")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(text.replace(old, new, 1))


# Keep this tiny CI-side source patch isolated until the tick path is proven on
# Windows. Once accepted, it can be folded directly into launcher/main.c.
launcher = "launcher/main.c"
replace_once(
    launcher,
    """        case OPENSPM_RUNTIME_EVENT_CORE_STOPPED:
            openspm_mods_event(host_context->mods, OPENSPM_MOD_EVENT_RUNTIME_STOPPED, event_data);
            break;
        default:
""",
    """        case OPENSPM_RUNTIME_EVENT_CORE_STOPPED:
            openspm_mods_event(host_context->mods, OPENSPM_MOD_EVENT_RUNTIME_STOPPED, event_data);
            break;
        case OPENSPM_RUNTIME_EVENT_TICK_BEGIN:
            openspm_mods_event(host_context->mods, OPENSPM_MOD_EVENT_TICK_BEGIN, event_data);
            break;
        case OPENSPM_RUNTIME_EVENT_TICK_END:
            openspm_mods_event(host_context->mods, OPENSPM_MOD_EVENT_TICK_END, event_data);
            break;
        default:
""",
    "launcher runtime tick event bridge",
)

print("M13 phase5: launcher runtime tick bridge staged")
