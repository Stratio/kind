FROM stratio/cloud-testing-suite:0.1.0-SNAPSHOT

VOLUME /var/lib/docker

ADD bin/cloud-provisioner.tar.gz /CTS/resources/

RUN chmod -R 0700 /CTS

CMD ["bash"]