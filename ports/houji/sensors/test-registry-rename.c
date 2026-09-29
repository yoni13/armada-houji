#define _GNU_SOURCE
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "hexagonfs.h"
int main(void) {
 char dir[]="/tmp/houji-ssc-rw-XXXXXX",path[512],buf[32]={0};
 assert(mkdtemp(dir));
 snprintf(path,sizeof(path),"%s/sub",dir);assert(!mkdir(path,0700));
 struct hexagonfs_dirent ent={.name="/",.ops=&hexagonfs_mapped_rw_ops,.u.phys=dir};
 struct hexagonfs_fd *fds[HEXAGONFS_MAX_FD]={0};
 int root=hexagonfs_open_root(fds,&ent);assert(root>=0);
 int fd=hexagonfs_openat_mode(fds,root,root,"sub/DIR",O_WRONLY|O_CREAT|O_TRUNC);assert(fd>=0);
 assert(hexagonfs_write(fds,fd,3,"DIR")==3);assert(!hexagonfs_close(fds,fd));
 fd=hexagonfs_openat_mode(fds,root,root,"sub/DIR",O_WRONLY|O_APPEND);assert(fd>=0);
 assert(hexagonfs_write(fds,fd,1,"\n")==1);assert(!hexagonfs_close(fds,fd));
 fd=hexagonfs_openat(fds,root,root,"sub/DIR");assert(fd>=0);
 assert(hexagonfs_read(fds,fd,sizeof(buf),buf)==4);assert(!memcmp(buf,"DIR\n",4));
 assert(hexagonfs_write(fds,fd,1,"!")<0);assert(!hexagonfs_close(fds,fd));
 assert(!hexagonfs_close(fds,root));
 ent.ops=&hexagonfs_mapped_rw_ops;
 root=hexagonfs_open_root(fds,&ent);assert(root>=0);
 assert(!hexagonfs_rename(fds,root,root,"/sub/DIR","/sub/renamed"));
 fd=hexagonfs_openat(fds,root,root,"sub/renamed");assert(fd>=0);
 memset(buf,0,sizeof(buf));assert(hexagonfs_read(fds,fd,sizeof(buf),buf)==4);
 assert(!memcmp(buf,"DIR\n",4));assert(!hexagonfs_close(fds,fd));
 assert(!hexagonfs_rename(fds,root,root,"/sub/renamed","/sub/DIR"));
 assert(hexagonfs_rename(fds,root,root,"/sub/DIR","./..")<0);
 assert(!hexagonfs_close(fds,root));
 ent.ops=&hexagonfs_mapped_ops;
 root=hexagonfs_open_root(fds,&ent);assert(root>=0);
 assert(hexagonfs_openat_mode(fds,root,root,"sub/DIR",O_WRONLY|O_TRUNC)<0);
 assert(hexagonfs_rename(fds,root,root,"/sub/DIR","/sub/renamed")<0);
 assert(!hexagonfs_close(fds,root));
 snprintf(path,sizeof(path),"%s/sub/DIR",dir);assert(!unlink(path));snprintf(path,sizeof(path),"%s/sub",dir);assert(!rmdir(path));assert(!rmdir(dir));
 puts("Registry create, write, append, read and read-only protection passed");
}
