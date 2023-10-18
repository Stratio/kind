FROM stratio/cloud-testing-suite:0.1.0-SNAPSHOT

RUN apt-get update && apt-get -y install sudo

RUN useradd -m docker && echo "docker:docker" | chpasswd && adduser docker sudo

USER docker

COPY bin/cloud-provisioner.tar.gz /CTS/resources/bin/

CMD ["bash"]