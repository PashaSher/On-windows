/* ALSA capture → HTTP/1.1 chunked POST. cc -O2 -o audio_relay_tunnel audio_relay_tunnel.c -lasound */
#include <alsa/asoundlib.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define SAMPLE_RATE 16000
#define FRAME_MS 20
#define CHUNK_BYTES (SAMPLE_RATE * 2 * FRAME_MS / 1000)

typedef struct {
    char host[256];
    char path[512];
    int port;
} UrlParts;

static volatile sig_atomic_t g_stop;

static void on_sig(int sig) {
    (void)sig;
    g_stop = 1;
}

static int parse_url(const char *raw, UrlParts *u) {
    const char *p = raw;
    u->port = 80;
    if (strncmp(p, "http://", 7) == 0) {
        p += 7;
    } else if (strncmp(p, "https://", 8) == 0) {
        fprintf(stderr, "https publish url not supported\n");
        return -1;
    }
    const char *slash = strchr(p, '/');
    const char *colon = strchr(p, ':');
    if (slash && colon && colon < slash) {
        size_t hl = (size_t)(colon - p);
        if (hl >= sizeof(u->host)) {
            return -1;
        }
        memcpy(u->host, p, hl);
        u->host[hl] = 0;
        u->port = atoi(colon + 1);
        snprintf(u->path, sizeof(u->path), "%s", slash);
    } else if (slash) {
        size_t hl = (size_t)(slash - p);
        if (hl >= sizeof(u->host)) {
            return -1;
        }
        memcpy(u->host, p, hl);
        u->host[hl] = 0;
        snprintf(u->path, sizeof(u->path), "%s", slash);
    } else {
        snprintf(u->host, sizeof(u->host), "%s", p);
        u->path[0] = '/';
        u->path[1] = 0;
    }
    return 0;
}

static int connect_host(const char *host, int port) {
    char portstr[16];
    struct addrinfo hints, *res, *rp;
    snprintf(portstr, sizeof(portstr), "%d", port);
    memset(&hints, 0, sizeof(hints));
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, portstr, &hints, &res) != 0) {
        return -1;
    }
    int fd = -1;
    for (rp = res; rp; rp = rp->ai_next) {
        fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
        if (fd < 0) {
            continue;
        }
        if (connect(fd, rp->ai_addr, rp->ai_addrlen) == 0) {
            int one = 1;
            setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
            break;
        }
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    return fd;
}

static int write_all(int fd, const void *buf, size_t len) {
    const char *p = (const char *)buf;
    while (len) {
        ssize_t n = write(fd, p, len);
        if (n <= 0) {
            return -1;
        }
        p += n;
        len -= (size_t)n;
    }
    return 0;
}

static int send_chunk(int fd, const void *data, size_t len) {
    char hdr[32];
    int hlen = snprintf(hdr, sizeof(hdr), "%x\r\n", (unsigned)len);
    if (write_all(fd, hdr, (size_t)hlen) != 0) {
        return -1;
    }
    if (len && write_all(fd, data, len) != 0) {
        return -1;
    }
    return write_all(fd, "\r\n", 2);
}

static snd_pcm_t *open_capture(const char *device) {
    snd_pcm_t *pcm = NULL;
    if (snd_pcm_open(&pcm, device, SND_PCM_STREAM_CAPTURE, 0) < 0) {
        fprintf(stderr, "cannot open alsa device %s\n", device);
        return NULL;
    }
    if (snd_pcm_set_params(
            pcm,
            SND_PCM_FORMAT_S16_LE,
            SND_PCM_ACCESS_RW_INTERLEAVED,
            1,
            SAMPLE_RATE,
            1,
            FRAME_MS * 1000) < 0) {
        fprintf(stderr, "cannot set alsa params on %s\n", device);
        snd_pcm_close(pcm);
        return NULL;
    }
    return pcm;
}

static int publish_stream(
    UrlParts *u,
    const char *token,
    snd_pcm_t *pcm,
    int sock) {
    char req[2048];
    int rlen = snprintf(
        req,
        sizeof(req),
        "POST %s HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Authorization: Bearer %s\r\n"
        "Content-Type: application/octet-stream\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Connection: close\r\n\r\n",
        u->path,
        u->host,
        token);
    if (write_all(sock, req, (size_t)rlen) != 0) {
        return -1;
    }

    int16_t buf[CHUNK_BYTES / 2];
    size_t total = 0;
    while (!g_stop) {
        snd_pcm_sframes_t n = snd_pcm_readi(pcm, buf, (snd_pcm_uframes_t)(CHUNK_BYTES / 2));
        if (n == -EPIPE) {
            snd_pcm_prepare(pcm);
            continue;
        }
        if (n < 0) {
            fprintf(stderr, "alsa read error: %s\n", snd_strerror((int)n));
            return -1;
        }
        if (n == 0) {
            continue;
        }
        size_t bytes = (size_t)n * 2;
        if (send_chunk(sock, buf, bytes) != 0) {
            return -1;
        }
        total += bytes;
    }
    send_chunk(sock, "", 0);
    write_all(sock, "0\r\n\r\n", 6);
    fprintf(stderr, "uploaded %zu pcm bytes\n", total);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <publish-url> <bearer-token> [alsa-device]\n", argv[0]);
        return 2;
    }
    const char *device = (argc >= 4 && argv[3][0]) ? argv[3] : "plughw:2,0";
    UrlParts u;
    if (parse_url(argv[1], &u) != 0) {
        return 1;
    }

    signal(SIGINT, on_sig);
    signal(SIGTERM, on_sig);

    snd_pcm_t *pcm = open_capture(device);
    if (!pcm) {
        return 1;
    }

    fprintf(
        stderr,
        "audio tunnel: %s @ %dHz → %s (device=%s)\n",
        "pcm_s16le",
        SAMPLE_RATE,
        argv[1],
        device);

    while (!g_stop) {
        int sock = connect_host(u.host, u.port);
        if (sock < 0) {
            perror("connect");
            sleep(1);
            continue;
        }
        if (publish_stream(&u, argv[2], pcm, sock) != 0) {
            close(sock);
            if (!g_stop) {
                sleep(1);
            }
            continue;
        }
        close(sock);
    }

    snd_pcm_close(pcm);
    return 0;
}
