/* stdin → HTTP/1.1 chunked POST (PCM/OGG relay). Компиляция: cc -O2 -o audio_relay_publish */
#include <arpa/inet.h>
#include <netdb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define BUF_SZ 4096

typedef struct {
    char host[256];
    char path[512];
    int port;
    int ssl;
} UrlParts;

static int parse_url(const char *raw, UrlParts *u) {
    const char *p = raw;
    u->ssl = 0;
    u->port = 80;
    if (strncmp(p, "http://", 7) == 0) {
        p += 7;
    } else if (strncmp(p, "https://", 8) == 0) {
        p += 8;
        u->ssl = 1;
        u->port = 443;
        fprintf(stderr, "https not supported in minimal publisher\n");
        return -1;
    }
    const char *slash = strchr(p, '/');
    const char *colon = strchr(p, ':');
    if (slash && colon && colon < slash) {
        size_t hl = (size_t)(colon - p);
        if (hl >= sizeof(u->host)) return -1;
        memcpy(u->host, p, hl);
        u->host[hl] = 0;
        u->port = atoi(colon + 1);
        snprintf(u->path, sizeof(u->path), "%s", slash);
    } else if (slash) {
        size_t hl = (size_t)(slash - p);
        if (hl >= sizeof(u->host)) return -1;
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
    if (getaddrinfo(host, portstr, &hints, &res) != 0) return -1;
    int fd = -1;
    for (rp = res; rp; rp = rp->ai_next) {
        fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
        if (fd < 0) continue;
        if (connect(fd, rp->ai_addr, rp->ai_addrlen) == 0) break;
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
        if (n <= 0) return -1;
        p += n;
        len -= (size_t)n;
    }
    return 0;
}

static int send_chunk(int fd, const void *data, size_t len) {
    char hdr[32];
    int hlen = snprintf(hdr, sizeof(hdr), "%x\r\n", (unsigned)len);
    if (write_all(fd, hdr, (size_t)hlen) != 0) return -1;
    if (len && write_all(fd, data, len) != 0) return -1;
    return write_all(fd, "\r\n", 2);
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s <publish-url> <bearer-token> <content-type>\n", argv[0]);
        return 2;
    }
    UrlParts u;
    if (parse_url(argv[1], &u) != 0) return 1;

    int fd = connect_host(u.host, u.port);
    if (fd < 0) {
        perror("connect");
        return 1;
    }

    char req[2048];
    int rlen = snprintf(
        req,
        sizeof(req),
        "POST %s HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Authorization: Bearer %s\r\n"
        "Content-Type: %s\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Connection: close\r\n\r\n",
        u.path,
        u.host,
        argv[2],
        argv[3]);
    if (write_all(fd, req, (size_t)rlen) != 0) {
        perror("write headers");
        close(fd);
        return 1;
    }

    unsigned char buf[BUF_SZ];
    size_t total = 0;
    for (;;) {
        ssize_t n = read(STDIN_FILENO, buf, sizeof(buf));
        if (n < 0) break;
        if (n == 0) break;
        if (send_chunk(fd, buf, (size_t)n) != 0) break;
        total += (size_t)n;
    }
    send_chunk(fd, "", 0);
    write_all(fd, "0\r\n\r\n", 6);

    char resp[512];
    ssize_t rn = read(fd, resp, sizeof(resp) - 1);
    if (rn > 0) {
        resp[rn] = 0;
        fprintf(stderr, "response: %.120s\n", resp);
    }
    fprintf(stderr, "uploaded %zu bytes\n", total);
    close(fd);
    return 0;
}
